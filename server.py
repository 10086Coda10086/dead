#!/usr/bin/python3
# coding: utf-8

# show welcome text
print(f'''
Welcome to Sleepy 2025!
Give us a Star 🌟 please: https://github.com/wyf9/sleepy
Bug Report: https://wyf9.top/t/sleepy/bug
Feature Request: https://wyf9.top/t/sleepy/feature
Security Report: https://wyf9.top/t/sleepy/security
'''[1:])

# import modules
try:
    import time
    import os
    from datetime import datetime
    from functools import wraps

    import flask
    import json5
    import pytz
    from markupsafe import escape
    from jinja2 import FileSystemLoader, ChoiceLoader

    from config import Config as config_init
    from utils import Utils as utils_init
    from data import Data as data_init
    from plugin import Plugin as plugin_init
    import _utils
except:
    print(f'''
Import module Failed!
 * Please make sure you installed all dependencies in requirements.txt
 * If you believe that's our fault, report the bug to us: https://wyf9.top/t/sleepy/bug
 * And provide the logs (below) to us:
'''[1:-1])
    raise


class ThemeLoader(ChoiceLoader):
    '''
    自定义主题加载器，支持主题 fallback 机制
    当主题中的文件缺失时，自动 fallback 到 default 主题
    '''

    def __init__(self, theme_name):
        self.theme_name = theme_name
        self.default_theme = 'default'
        loaders = []

        # 添加当前主题的加载器
        if theme_name != self.default_theme:
            loaders.append(FileSystemLoader(f'theme/{theme_name}'))

        # 添加默认主题的加载器作为 fallback
        loaders.append(FileSystemLoader(f'theme/{self.default_theme}'))

        super().__init__(loaders)


try:
    # init flask app
    app = flask.Flask(__name__,
                      template_folder='theme/default',
                      static_folder=None)

    # init config
    c = config_init()

    if c.main.debug:
        # debug: disable template cache
        app.config['TEMPLATES_AUTO_RELOAD'] = True
        app.jinja_env.auto_reload = True
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    else:
        # not debug: disable flask access log
        from logging import getLogger
        flask_default_logger = getLogger('werkzeug')
        flask_default_logger.disabled = True

    # init utils
    u = utils_init(config=c)

    # init data
    d = data_init(
        config=c,
        utils=u
    )
    d.load()
    d.start_timer_check(data_check_interval=c.main.checkdata_interval)  # 启动定时保存 data

    # init metrics if enabled
    if c.metrics.enabled:
        d.metrics_init()
        u.info('[metrics] metrics enabled, open /metrics to see the count.')

    # init plugin
    p = plugin_init(
        config=c,
        utils=u,
        data=d,
        app=app
    )

except KeyboardInterrupt:
    u.info('Interrupt init, quitting')
    exit(0)
except _utils.SleepyException as e:
    u.error(e)
    exit(1)
except:
    u.error('Unexpected Error!')
    raise


# --- Functions


def get_theme(template_name=None):
    """
    获取主题并检查其是否存在

    :param template_name: 模板文件名，如 'index.html', 'panel.html', 'login.html'（可选，用于日志记录）
    :return str: 主题名称
    """
    # 获取主题 (优先使用 URL 参数，其次是 cookie，最后是配置文件)
    theme = flask.request.args.get('theme') or flask.request.cookies.get('sleepy-theme') or c.page.theme

    # 检查主题目录是否存在，如果不存在则使用默认主题
    if not os.path.exists(os.path.join('theme', theme)):
        if template_name:
            u.warning(f"Theme directory {theme} not found for {template_name}, using default theme")
        else:
            u.warning(f"Theme directory {theme} not found, using default theme")
        theme = getattr(c.page, 'theme', 'default')
        if not os.path.exists(os.path.join('theme', theme)):
            theme = 'default'

    # 设置自定义的主题加载器，支持 fallback 机制
    app.jinja_loader = ThemeLoader(theme)

    # 设置静态文件夹
    app.static_folder = f'theme/{theme}/static'

    return theme


# 全局静态文件处理函数，支持 fallback 机制
@app.route('/static/<path:filename>', endpoint='static')
def static_proxy(filename):
    # 获取当前主题
    theme = flask.request.args.get('theme') or flask.request.cookies.get('sleepy-theme') or c.page.theme

    # 首先尝试从当前主题加载
    theme_path = os.path.join('theme', theme, 'static', filename)
    if os.path.exists(theme_path):
        response = flask.send_from_directory(f'theme/{theme}/static', filename)
        # 设置缓存控制头，防止浏览器缓存
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    # 如果当前主题中不存在，fallback 到默认主题
    default_path = os.path.join('theme', 'default', 'static', filename)
    if os.path.exists(default_path):
        u.debug(f'Static file {filename} not found in theme {theme}, using default theme\'s file')
        response = flask.send_from_directory('theme/default/static', filename)
        # 设置缓存控制头，防止浏览器缓存
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    # 如果默认主题中也不存在，返回 404
    u.warning(f'Static file {filename} not found in any theme')
    return flask.abort(404)


@app.before_request
def before_request():
    '''
    在日志中显示 ip, 并记录 metrics 信息

    :param req: `flask.request` 对象, 用于取 ip
    :param msg: 信息 (一般是路径, 同时作为 metrics 的项名)
    '''
    # --- get path
    path = flask.request.path
    # --- log
    ip1 = flask.request.remote_addr
    ip2 = flask.request.headers.get('X-Forwarded-For')
    if ip2:
        u.info(f'- Request: {ip1} / {ip2} : {path}')
    else:
        u.info(f'- Request: {ip1} : {path}')
    # --- count
    if c.metrics.enabled:
        d.record_metrics(path)


@app.after_request
def after_request(response: flask.Response):
    '''
    在响应中设置主题 cookie
    '''
    # 如果 URL 中有主题参数，将其保存到 cookie 中
    if hasattr(flask.g, 'theme_from_url'):
        theme = flask.g.theme_from_url
        # 设置 cookie，有效期 30 天
        response.set_cookie('sleepy-theme', theme, max_age=30*24*60*60, path='/', samesite='Lax')
    return response


def require_secret(view_func):
    '''
    require_secret 修饰器, 用于指定函数需要 secret 鉴权
    - ***请确保修饰器紧跟函数定义，如:***
    ```
    @app.route('/set')
    @require_secret
    def set_normal(): ...
    ```
    '''
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        # 1. body
        # -> {"secret": "my-secret"}
        body: dict = flask.request.get_json(silent=True) or {}
        if body and body.get('secret') == c.main.secret:
            u.debug('[Auth] Verify secret Success from Body')
            return view_func(*args, **kwargs)

        # 2. param
        # -> ?secret=my-secret
        elif flask.request.args.get('secret') == c.main.secret:
            u.debug('[Auth] Verify secret Success from Param')
            return view_func(*args, **kwargs)

        # 3. header (Sleepy-Secret)
        # -> Sleepy-Secret: my-secret
        elif flask.request.headers.get('Sleepy-Secret') == c.main.secret:
            u.debug('[Auth] Verify secret Success from Header (Sleepy-Secret)')
            return view_func(*args, **kwargs)

        # 4. header (Authorization)
        # -> Authorization: Bearer my-secret
        elif flask.request.headers.get('Authorization', '')[7:] == c.main.secret:
            u.debug('[Auth] Verify secret Success from Header (Authorization)')
            return view_func(*args, **kwargs)

        # 5. cookie (sleepy-token)
        # -> Cookie: sleepy-token=my-secret
        elif flask.request.cookies.get('sleepy-token') == c.main.secret:
            u.debug('[Auth] Verify secret Success from Cookie (sleepy-token)')
            return view_func(*args, **kwargs)

        # -1. no any secret
        else:
            u.debug('[Auth] Verify secret Failed')
            return u.reterr(
                code='not authorized',
                message='wrong secret'
            ), 401
    return wrapped_view

# --- Templates


@app.route('/')
def index():
    '''
    根目录返回 html
    - Method: **GET**
    '''
    # 获取手动状态
    try:
        status: dict = c.status.status_list[d.data['status']]
    except:
        u.warning(f"Index {d.data['status']} out of range!")
        status = {
            'name': 'Unknown',
            'desc': '未知的标识符，可能是配置问题。',
            'color': 'error'
        }
    # 获取更多信息 (more_text)
    more_text: str = c.page.more_text
    if c.metrics.enabled:
        more_text = more_text.format(
            visit_today=d.data['metrics']['today'].get('/', 0),
            visit_month=d.data['metrics']['month'].get('/', 0),
            visit_year=d.data['metrics']['year'].get('/', 0),
            visit_total=d.data['metrics']['total'].get('/', 0)
        )
    # 获取主题
    theme = get_theme('index.html')

    # 处理插件注入
    plugin_templates: list[tuple[str, str]] = []
    for i in p.plugins:
        if i[1]:
            plugin_templates.append((
                i[0],
                flask.render_template_string(
                    i[1],
                    c=i[3].config,
                    d=d.data,
                    u=u,
                    current_theme=theme
                )))

    # 返回 html
    return flask.render_template(
        'index.html',
        c=c,
        more_text=more_text,
        status=status,
        last_updated=d.data['last_updated'],
        plugins=plugin_templates,
        current_theme=theme,
        available_themes=u.themes_available
    ), 200


@app.route('/'+'git'+'hub')
def git_hub():
    '''
    这里谁来了都改不了!
    '''
    return flask.redirect('ht'+'tps:'+'//git'+'hub.com/'+'wyf'+'9/sle'+'epy', 301)


@app.route('/none')
def none():
    '''
    返回 204 No Content, 可用于 Uptime Kuma 等工具监控服务器状态使用
    '''
    return '', 204


# --- Read-only


@app.route('/query')
def query(ret_as_dict: bool = False):
    '''
    获取当前状态
    - 无需鉴权
    - Method: **GET**

    :param ret_as_dict: 使函数直接返回 dict 而非 `u.format_dict()` 格式化后的 response
    '''
    # 获取手动状态
    st: int = d.data['status']
    try:
        stinfo = c.status.status_list[st]
    except:
        stinfo = {
            'id': -1,
            'name': '[未知]',
            'desc': f'未知的标识符 {st}，可能是配置问题。',
            'color': 'error'
        }
    # 获取设备状态
    if d.data['private_mode']:
        # 隐私模式
        devicelst = {}
    elif c.status.using_first:
        # 使用中优先
        devicelst = {}  # devicelst = device_using
        device_not_using = {}
        for n in d.data['device_status']:
            i = d.data['device_status'][n]
            if i['using']:
                devicelst[n] = i
            else:
                device_not_using[n] = i
        if c.status.sorted:
            devicelst = dict(sorted(devicelst.items()))
            device_not_using = dict(sorted(device_not_using.items()))
        devicelst.update(device_not_using)  # append not_using items to end
    else:
        # 正常获取
        devicelst: dict = d.data['device_status']
        if c.status.sorted:
            devicelst = dict(sorted(devicelst.items()))

    # 构造返回
    timenow = datetime.now(pytz.timezone(c.main.timezone))
    ret = {
        'time': timenow.strftime('%Y-%m-%d %H:%M:%S'),
        'timezone': c.main.timezone,
        'success': True,
        'status': st,
        'info': stinfo,
        'device': devicelst,
        'device_status_slice': c.status.device_slice,
        'last_updated': d.data['last_updated'],
        'refresh': c.status.refresh_interval
    }
    if ret_as_dict:
        return ret
    else:
        return u.format_dict(ret), 200


@app.route('/status_list')
def get_status_list():
    '''
    获取 `status_list`
    - 无需鉴权
    - Method: **GET**
    '''
    return u.format_dict(c.status.status_list), 200


# --- Status API


@app.route('/set')
@require_secret
def set_normal():
    '''
    设置状态
    - http[s]://<your-domain>[:your-port]/set?status=<a-number>
    - Method: **GET**
    '''
    status = escape(flask.request.args.get('status'))
    try:
        status = int(status)
    except:
        return u.reterr(
            code='bad request',
            message="argument 'status' must be int"
        ), 400
    old_status = d.data['status']
    d.data['status'] = status

    # 触发状态更新事件
    p.trigger_event('status_updated', old_status, status)

    return u.format_dict({
        'success': True,
        'code': 'OK',
        'set_to': status
    }), 200


# --- Device API

@app.route('/device/set', methods=['GET', 'POST'])
@require_secret
def device_set():
    '''
    设置单个设备的信息/打开应用
    - Method: **GET / POST**
    '''
    if flask.request.method == 'GET':
        try:
            device_id = escape(flask.request.args.get('id'))
            device_show_name = escape(flask.request.args.get('show_name'))
            device_using = _utils.tobool(escape(flask.request.args.get('using')), throw=True)
            app_name = escape(flask.request.args.get('app_name'))
        except:
            return u.reterr(
                code='bad request',
                message='missing param or wrong param type'
            ), 400
    elif flask.request.method == 'POST':
        req = flask.request.get_json()
        try:
            device_id = req['id']
            device_show_name = req['show_name']
            device_using = _utils.tobool(req['using'], throw=True)
            app_name = req['app_name']
        except:
            return u.reterr(
                code='bad request',
                message='missing param or wrong param type'
            ), 400
    devices: dict = d.data['device_status']
    if (not device_using) and c.status.not_using:
        # 如未在使用且锁定了提示，则替换
        app_name = c.status.not_using
    devices[device_id] = {
        'show_name': device_show_name,
        'using': device_using,
        'app_name': app_name
    }
    d.data['last_updated'] = datetime.now(pytz.timezone(c.main.timezone)).strftime('%Y-%m-%d %H:%M:%S')
    d.check_device_status()

    # 触发设备更新事件
    p.trigger_event('device_updated', device_id, d.data['device_status'][device_id])

    return u.format_dict({
        'success': True,
        'code': 'OK'
    }), 200


@app.route('/device/remove')
@require_secret
def remove_device():
    '''
    移除单个设备的状态
    - Method: **GET**
    '''
    device_id = escape(flask.request.args.get('id'))
    try:
        # 保存设备信息用于事件触发
        device_info = d.data['device_status'][device_id].copy() if device_id in d.data['device_status'] else None

        del d.data['device_status'][device_id]
        d.data['last_updated'] = datetime.now(pytz.timezone(c.main.timezone)).strftime('%Y-%m-%d %H:%M:%S')
        d.check_device_status()

        # 触发设备删除事件
        if device_info:
            p.trigger_event('device_removed', device_id, device_info)

    except KeyError:
        return u.reterr(
            code='not found',
            message='cannot find item'
        ), 404
    return u.format_dict({
        'success': True,
        'code': 'OK'
    }), 200


@app.route('/device/clear')
@require_secret
def clear_device():
    '''
    清除所有设备状态
    - Method: **GET**
    '''
    # 保存设备信息用于事件触发
    old_devices = d.data['device_status'].copy()

    d.data['device_status'] = {}
    d.data['last_updated'] = datetime.now(pytz.timezone(c.main.timezone)).strftime('%Y-%m-%d %H:%M:%S')
    d.check_device_status()

    # 触发设备清除事件
    p.trigger_event('devices_cleared', old_devices)

    return u.format_dict({
        'success': True,
        'code': 'OK'
    }), 200


@app.route('/device/private_mode')
@require_secret
def private_mode():
    '''
    隐私模式, 即不在返回中显示设备状态 (仍可正常更新)
    - Method: **GET**
    '''
    private = _utils.tobool(escape(flask.request.args.get('private')))
    if private == None:
        return u.reterr(
            code='invaild request',
            message='"private" arg only supports boolean type'
        ), 400
    old_private_mode = d.data.get('private_mode', False)
    d.data['private_mode'] = private
    d.data['last_updated'] = datetime.now(pytz.timezone(c.main.timezone)).strftime('%Y-%m-%d %H:%M:%S')

    # 触发隐私模式切换事件
    p.trigger_event('private_mode_changed', old_private_mode, private)

    return u.format_dict({
        'success': True,
        'code': 'OK'
    }), 200


@app.route('/save_data')
@require_secret
def save_data():
    '''
    保存内存中的状态信息到 `data/data.json`
    - Method: **GET**
    '''
    try:
        d.save()

        # 触发数据保存事件
        p.trigger_event('data_saved', d.data)

    except Exception as e:
        return u.reterr(
            code='exception',
            message=f'{e}'
        ), 500
    return u.format_dict({
        'success': True,
        'code': 'OK',
        'data': d.data
    }), 200


@app.route('/events')
def events():
    '''
    SSE 事件流，用于推送状态更新
    - Method: **GET**
    '''
    last_event_id = flask.request.headers.get('Last-Event-ID', 0)

    def event_stream(event_id: int = last_event_id):
        last_update = None
        last_heartbeat = time.time()

        while True:
            current_time = time.time()
            # 检查数据是否已更新
            current_update = d.data['last_updated']

            # 如果数据有更新，发送更新事件并重置心跳计时器
            if last_update != current_update:
                last_update = current_update
                # 重置心跳计时器
                last_heartbeat = current_time

                # 获取 /query 返回数据
                update_data = json5.dumps(query(ret_as_dict=True), quote_keys=True, ensure_ascii=False)
                event_id += 1
                yield f'id: {event_id}\nevent: update\ndata: {update_data}\n\n'

            # 只有在没有数据更新的情况下才检查是否需要发送心跳
            elif current_time - last_heartbeat >= 30:
                event_id += 1
                yield f'id: {event_id}\nevent: heartbeat\ndata:\n\n'
                last_heartbeat = current_time

            time.sleep(1)  # 每秒检查一次更新

    response = flask.Response(event_stream(last_event_id), mimetype='text/event-stream', status=200)
    response.headers['Cache-Control'] = 'no-cache'  # 禁用缓存
    response.headers['X-Accel-Buffering'] = 'no'  # 禁用 Nginx 缓冲
    return response

# --- WebUI (Admin Panel)


@app.route('/webui/panel')
@require_secret
def admin_panel():
    '''
    管理面板
    - Method: **GET**
    '''
    # 获取主题
    theme = get_theme('panel.html')

    # 获取插件注册的管理后台卡片
    plugin_admin_cards = p.get_admin_cards()

    # 渲染插件卡片内容
    rendered_cards = []
    for card in plugin_admin_cards:
        try:
            # 渲染卡片内容（如果是模板字符串）
            if isinstance(card['content'], str) and '{{' in card['content']:
                card_content = flask.render_template_string(
                    card['content'],
                    c=c,
                    d=d.data,
                    u=u,
                    current_theme=theme
                )
            else:
                card_content = card['content']

            rendered_cards.append({
                'id': card['id'],
                'plugin_name': card['plugin_name'],
                'title': card['title'],
                'content': card_content
            })
        except Exception as e:
            u.error(f"Error rendering admin card '{card['title']}' for plugin '{card['plugin_name']}': {e}")

    return flask.render_template(
        'panel.html',
        c=c,
        d=d.data,
        current_theme=theme,
        available_themes=u.themes_available,
        plugin_admin_cards=rendered_cards
    ), 200


@app.route('/webui/login')
def login():
    '''
    登录页面
    - Method: **GET**
    '''
    # 获取主题
    theme = get_theme('login.html')

    # 检查是否已经登录（cookie 中是否有有效的 sleepy-token）
    cookie_token = flask.request.cookies.get('sleepy-token')
    if cookie_token == c.main.secret:
        # 如果 cookie 有效，直接重定向到管理面板
        return flask.redirect('/webui/panel')

    return flask.render_template(
        'login.html',
        c=c,
        current_theme=theme
    ), 200


@app.route('/webui/auth', methods=['POST'])
def auth():
    '''
    处理登录请求，验证密钥并设置 cookie
    - Method: **POST**
    '''
    # 获取请求中的密钥
    body = flask.request.get_json(silent=True) or {}
    secret = body.get('secret')

    # 验证密钥
    if secret == c.main.secret:
        # 创建响应
        response = flask.make_response(u.format_dict({
            'success': True,
            'code': 'OK',
            'message': 'Login successful'
        }))

        # 设置 cookie，有效期为 30 天
        max_age = 30 * 24 * 60 * 60  # 30 days in seconds
        response.set_cookie('sleepy-token', secret, max_age=max_age, httponly=True, samesite='Lax')

        u.debug('[Auth] Login successful, cookie set')
        return response, 200
    else:
        u.debug('[Auth] Login failed, wrong secret')
        return u.reterr(
            code='not authorized',
            message='wrong secret'
        ), 401


@app.route('/webui/logout')
def logout():
    '''
    处理退出登录请求，清除 cookie
    - Method: **GET**
    '''
    # 创建响应
    response = flask.make_response(flask.redirect('/webui/login'))

    # 清除认证 cookie
    response.delete_cookie('sleepy-token')

    u.debug('[Auth] Logout successful')
    return response


@app.route('/api/login', methods=['GET', 'POST'])
@require_secret
def api_login():
    '''
    API登录接口，验证密钥并返回成功
    - Method: **GET / POST**
    '''
    u.debug('[API] Secret verified')
    return u.format_dict({
        'success': True,
        'code': 'OK',
        'message': 'Secret verified'
    }), 200


# --- Special

if c.metrics.enabled:
    @app.route('/metrics')
    def metrics():
        '''
        获取统计信息
        - Method: **GET**
        '''
        resp = d.get_metrics_resp()
        return resp, 200

# if c.util.steam_enabled:
#     @app.route('/steam-iframe')
#     def steam():
#         return flask.render_template(
#             'steam-iframe.html',
#             c=c,
#             steamids=c.util.steam_ids,
#             steam_refresh_interval=c.util.steam_refresh_interval
#         ), 200

# --- End

if __name__ == '__main__':
    u.info(f'=============== Hi {c.page.name}! ===============')
    u.info(f'Starting server: {f"[{c.main.host}]" if ":" in c.main.host else c.main.host}:{c.main.port}{" (debug enabled)" if c.main.debug else ""}')
    try:
        app.run(  # 启↗动↘
            host=c.main.host,
            port=c.main.port,
            debug=False,  # 此处禁用 Flask 提供的 debug 功能, 会导致代码执行两次
            threaded=True
        )

    except Exception as e:
        u.error(f"Error running server: {e}")
    print()
    u.info('Server exited, saving data...')
    d.save()
    u.info('Bye.')
