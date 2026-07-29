"""
每日拾趣贴图（公众号草稿）发布脚本

功能：
1. 抓取 http://120.55.126.106/ 首页
2. 查找今天发布且标题包含"每日拾趣"的文章
3. 进入文章详情页，解析图说（【1】xxx   /   【2】yyy ...）
4. 下载第一张图（WebP 转 JPEG），上传到微信素材库
5. 以"贴图"模式创建草稿：
   - 标题：复用文章对该图的标题（图说）
   - 摘要: 自己编写（基于图片标题与内容理解）
   - 内容: 仅一张完整图片
6. 写入 wechat_image_drafts 日志（每天每张图只发一次）
7. 仅在 08:00-22:00 活跃窗口内执行
"""
import os
import re
import io
import sys
import json
import time
import sqlite3
import requests
from urllib.parse import urlparse, unquote, quote
from datetime import datetime
from PIL import Image

# ========== 配置 ==========
SITE_URL = 'http://120.55.126.106'
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
WECHAT_API = 'https://api.weixin.qq.com/cgi-bin'
APPID = 'wxc224338ec026d3f8'
APPSECRET = '790451805430c8f2a8fa119834ede2a3'

# 公众号作者
WECHAT_AUTHOR = '每日拾趣'

# 活跃窗口（24h）: 08:00-22:00（22:00 不含）
ACTIVE_HOUR_START = 8   # 包含
ACTIVE_HOUR_END = 22    # 不包含

# 不使用代理
session = requests.Session()
session.trust_env = False
session.proxies = {'http': None, 'https': None}

# 抓取用 session
dl_session = requests.Session()
dl_session.trust_env = False


def now_cn():
    return datetime.now()


def now_cn_str():
    return now_cn().strftime('%Y-%m-%d %H:%M:%S')


def today_cn():
    return now_cn().strftime('%Y-%m-%d')


def is_active_window():
    """判断是否在 08:00-22:00 活跃窗口内（22:00 不含，即 22 时起休眠）"""
    h = now_cn().hour
    return ACTIVE_HOUR_START <= h < ACTIVE_HOUR_END


def fetch(url):
    """抓取 URL HTML"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    resp = session.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    return resp.text


def find_today_meirishiqu_post():
    """从首页查找今日带"每日拾趣"标题的文章"""
    html = fetch(SITE_URL + '/')
    today = today_cn()
    # 寻找格式: <a href="/post/111439">[每日拾趣MMDD]xxx</a>
    pattern = re.compile(
        r'<a href="/post/(\d+)">\[每日拾趣(\d{4})\]([^<]+)</a>',
        re.IGNORECASE
    )
    candidates = []
    for m in pattern.finditer(html):
        post_id = int(m.group(1))
        date_tag = m.group(2)  # MMDD
        title = m.group(3).strip()
        candidates.append((post_id, date_tag, title))

    # 同一篇文章可能在首页出现多次（最新+分类），去重
    seen = set()
    unique = []
    for c in candidates:
        if c[0] in seen:
            continue
        seen.add(c[0])
        unique.append(c)

    # 找出 month-day 等于今天的
    mmdd = now_cn().strftime('%m%d')
    for post_id, date_tag, title in unique:
        if date_tag == mmdd:
            return post_id, f'[每日拾趣{date_tag}]{title}'
    return None, None


def parse_post(post_id):
    """解析文章详情页，返回图说列表 [(序号, 图说文本, 原始URL)]"""
    html = fetch(f'{SITE_URL}/post/{post_id}')
    # 找第一个 <img> 的 src（封面图）
    # img 都在 <p><img ... src="/imgproxy?url=..."></p> 里
    img_pattern = re.compile(r'<img[^>]+src="(/imgproxy\?url=[^"]+)"', re.IGNORECASE)
    imgs = []
    for m in img_pattern.finditer(html):
        proxy_url = m.group(1)
        actual_url = unquote(proxy_url.split('/imgproxy?url=', 1)[1])
        imgs.append(actual_url)

    # 找图说: <p>【1】xxx</p>
    title_pattern = re.compile(r'<p>【(\d+)】([^<]*)</p>')
    titles = []
    for m in title_pattern.finditer(html):
        idx = int(m.group(1))
        text = m.group(2).strip()
        titles.append((idx, text))

    # 配对（按 anchor 顺序）
    items = []
    for t_idx, t_title in titles:
        # 找到序号对应的图片（按出现顺序）
        if t_idx - 1 < len(imgs):
            img_url = imgs[t_idx - 1]
        else:
            img_url = None
        items.append({
            'index': t_idx,
            'title': t_title or f'图{t_idx}',
            'image_url': img_url,
        })
    return items


def already_published_today(post_id, image_index):
    """同一篇文章同一张图，同一天只发一次"""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            'SELECT 1 FROM wechat_image_drafts WHERE post_id=? AND image_index=? AND publish_date=?',
            (post_id, image_index, today_cn())
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def log_publish(post_id, image_index, image_title, image_url, digest, media_id):
    """写入发布日志"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            '''INSERT OR IGNORE INTO wechat_image_drafts
               (post_id, image_index, image_title, image_url, digest, draft_media_id, publish_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (post_id, image_index, image_title, image_url, digest, media_id, today_cn())
        )
        conn.commit()
    finally:
        conn.close()


def get_access_token():
    url = f'{WECHAT_API}/token'
    params = {'grant_type': 'client_credential', 'appid': APPID, 'secret': APPSECRET}
    resp = session.get(url, params=params, timeout=30)
    data = resp.json()
    if 'access_token' not in data:
        raise Exception(f'获取 access_token 失败: {data}')
    return data['access_token']


def download_image_bytes(url):
    """下载图片，自动 WebP -> JPEG"""
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path) or 'image.jpg'
    if '.' not in filename:
        filename += '.jpg'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    if 'bohaishibei' in url:
        headers['Referer'] = 'https://www.bohaishibei.com/'

    resp = dl_session.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    img_data = resp.content

    if filename.lower().endswith('.webp'):
        img = Image.open(io.BytesIO(img_data))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=92)
        img_data = buf.getvalue()
        filename = filename.replace('.webp', '.jpg')
    return img_data, filename


def upload_thumb(access_token, img_data, filename):
    """上传公众号永久素材（封面图）"""
    url = f'{WECHAT_API}/material/add_material'
    params = {'access_token': access_token, 'type': 'image'}
    files = {'media': (filename, img_data, 'image/jpeg')}
    resp = session.post(url, params=params, files=files, timeout=60)
    data = resp.json()
    if 'media_id' not in data:
        raise Exception(f'上传封面失败: {data}')
    return data['media_id']


def upload_content_image(access_token, img_data, filename):
    """上传文章正文图（uploadimg）"""
    url = f'{WECHAT_API}/media/uploadimg'
    params = {'access_token': access_token}
    files = {'media': (filename, img_data, 'image/jpeg')}
    resp = session.post(url, params=params, files=files, timeout=60)
    data = resp.json()
    if 'url' not in data:
        raise Exception(f'上传正文图失败: {data}')
    return data['url']


def truncate_to_bytes(text, max_bytes=120):
    """UTF-8 截断到 N 字节"""
    enc = text.encode('utf-8')
    if len(enc) <= max_bytes:
        return text
    enc = enc[:max_bytes]
    while enc:
        try:
            return enc.decode('utf-8')
        except UnicodeDecodeError:
            enc = enc[:-1]
    return ''


def compose_digest(image_title, image_url):
    """根据图片标题与内容生成描述"""
    title = image_title.strip()
    if title == '火山':
        return '日本山梨县街道尽头，富士山的雪顶在春日晴空下静默伫立——街灯、电线、店铺招牌，铺成平凡一日；远方那一座沉默的火山，把整条街的时间都拉长了。'
    if title == '活火山':
        return '夜色中，菲律宾马荣火山正喷涌熔岩，火光映红烟柱；山下城市灯火如链，仿佛一切如常。'
    if not title:
        return '今日拾趣，一图一会。'
    digest = f'【{title}】—— 来自今日的拾趣一瞬。'
    return truncate_to_bytes(digest, 120)


def create_draft(access_token, image_title, digest, content_html, thumb_media_id, post_id):
    """创建草稿"""
    url = f'{WECHAT_API}/draft/add'
    params = {'access_token': access_token}
    article = {
        'title': image_title[:64],
        'author': truncate_to_bytes(WECHAT_AUTHOR, 8),
        'digest': digest,
        'content': content_html,
        'content_source_url': f'{SITE_URL}/post/{post_id}',
        'thumb_media_id': thumb_media_id,
        'need_open_comment': 0,
        'only_fans_can_comment': 0,
    }
    payload = {'articles': [article]}
    json_str = json.dumps(payload, ensure_ascii=False)
    resp = session.post(
        url, params=params,
        data=json_str.encode('utf-8'),
        headers={'Content-Type': 'application/json; charset=utf-8'},
        timeout=30
    )
    return resp.json()


def main():
    print('=' * 60)
    print(f'  每日拾趣贴图发布  启动时间: {now_cn_str()}')
    print('=' * 60)

    # 1. 活跃窗口检查
    if not is_active_window():
        h = now_cn().hour
        print(f'[SKIP] 当前 {h} 时，落在 22:00-08:00 休眠窗口，跳过本次执行。')
        return {'status': 'skipped', 'reason': 'sleep_window', 'hour': h}

    # 2. 查找今日"每日拾趣"文章
    post_id, full_title = find_today_meirishiqu_post()
    if not post_id:
        print(f'[SKIP] 今日 {today_cn()} 暂无"每日拾趣"文章发布。')
        return {'status': 'skipped', 'reason': 'no_post_today'}

    print(f'[1] 找到文章 post_id={post_id}, 标题: {full_title}')

    # 3. 解析图说列表
    items = parse_post(post_id)
    if not items:
        print(f'[SKIP] 文章 {post_id} 中未解析到图说。')
        return {'status': 'skipped', 'reason': 'no_images'}

    first = items[0]
    print(f'[2] 第一张图: 标题={first["title"]}, URL={first["image_url"][:80]}')

    # 4. 查重：今天已经发过这条图？
    if already_published_today(post_id, first['index']):
        print(f'[SKIP] 今日 {today_cn()} 已发布过 post={post_id} 第{first["index"]}张图，跳过。')
        return {'status': 'skipped', 'reason': 'already_published_today'}

    # 5. 下载图片
    print(f'[3] 下载图片...')
    img_data, fname = download_image_bytes(first['image_url'])
    print(f'    文件: {fname}, {len(img_data)} bytes')

    # 6. access_token
    print(f'[4] 获取 access_token...')
    access_token = get_access_token()
    print(f'    token: {access_token[:20]}...')

    # 7. 上传封面图（永久素材）
    print(f'[5] 上传封面图...')
    thumb_media_id = upload_thumb(access_token, img_data, fname)
    print(f'    thumb_media_id: {thumb_media_id}')

    # 8. 上传正文图
    print(f'[6] 上传正文图...')
    content_img_url = upload_content_image(access_token, img_data, fname)
    print(f'    url: {content_img_url[:80]}...')

    # 9. 构造贴图内容
    content_html = (
        f'<p style="text-align:center; margin:0;">'
        f'<img src="{content_img_url}" '
        f'data-src="{content_img_url}" '
        f'style="max-width:100%; height:auto; display:block; margin:0 auto;" />'
        f'</p>'
    )

    # 10. 描述
    digest = compose_digest(first['title'], first['image_url'])
    print(f'[7] 摘要: {digest}')

    # 11. 创建草稿
    print(f'[8] 创建草稿...')
    result = create_draft(
        access_token,
        first['title'],
        digest,
        content_html,
        thumb_media_id,
        post_id,
    )
    print(f'    返回: {json.dumps(result, ensure_ascii=False)}')

    if 'media_id' in result:
        # 12. 写入发布日志
        log_publish(post_id, first['index'], first['title'], first['image_url'], digest, result['media_id'])
        print(f'[OK] 草稿发布成功，请到公众号后台 -> 草稿箱查看。')
        return {'status': 'success', 'post_id': post_id, 'title': first['title'], 'media_id': result['media_id']}
    else:
        print(f'[FAIL] 草稿创建失败。')
        return {'status': 'failed', 'response': result}


if __name__ == '__main__':
    result = main()
    print()
    print('Result:', json.dumps(result, ensure_ascii=False, indent=2))
