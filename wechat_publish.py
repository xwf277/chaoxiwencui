"""
微信公众号草稿创建脚本
功能：从数据库读取文章 -> 下载图片 -> 上传到微信素材库 -> 创建草稿
"""
import sqlite3
import os
import re
import json
import time
import io
import requests
from urllib.parse import urlparse, parse_qs, unquote
from PIL import Image

# ========== 配置 ==========
APPID = 'wxc224338ec026d3f8'
APPSECRET = '790451805430c8f2a8fa119834ede2a3'
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
WECHAT_API = 'https://api.weixin.qq.com/cgi-bin'

# 创建不使用代理的 session（确保从本机公网IP发出请求）
session = requests.Session()
session.trust_env = False  # 忽略环境变量中的代理设置
session.proxies = {'http': None, 'https': None}

# 下载图片用的 session（允许代理）
dl_session = requests.Session()
dl_session.trust_env = False


def get_access_token():
    """获取微信 access_token"""
    url = f'{WECHAT_API}/token'
    params = {
        'grant_type': 'client_credential',
        'appid': APPID,
        'secret': APPSECRET
    }
    print('[1] 获取 access_token...')
    resp = session.get(url, params=params, timeout=30)
    data = resp.json()
    if 'access_token' in data:
        token = data['access_token']
        print(f'    access_token: {token[:20]}... (expires in {data.get("expires_in", 0)}s)')
        return token
    else:
        print(f'    ERROR: {json.dumps(data, ensure_ascii=False)}')
        raise Exception(f'获取 access_token 失败: {data}')


def get_article(post_id=None):
    """从数据库获取文章，可指定 post_id，默认取最新一篇"""
    if post_id:
        print(f'[2] 读取数据库文章 post_id={post_id}...')
    else:
        print('[2] 读取数据库第一篇文章...')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if post_id:
        post = c.execute('SELECT * FROM posts WHERE post_id = ?', (post_id,)).fetchone()
    else:
        post = c.execute('SELECT * FROM posts ORDER BY post_id DESC LIMIT 1').fetchone()
    conn.close()
    if not post:
        raise Exception(f'文章 post_id={post_id} 不存在')

    article = {
        'post_id': post['post_id'],
        'title': post['title'],
        'author': post['author'] or '每日拾趣',
        'content_html': post['content_html'] or '',
        'image_url': post['image_url'] or '',
        'excerpt': post['excerpt'] or '',
        'category': post['category'],
        'date': post['date'],
    }
    print(f'    文章ID: {article["post_id"]}')
    print(f'    标题: {article["title"]}')
    print(f'    作者: {article["author"]}')
    print(f'    正文长度: {len(article["content_html"])} 字符')
    print(f'    封面图: {article["image_url"][:80]}')
    return article


def convert_webp_to_jpeg(image_data):
    """将 WebP 图片数据转换为 JPEG 格式"""
    img = Image.open(io.BytesIO(image_data))
    # 如果有透明通道，转 RGB（JPEG 不支持 alpha）
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGB')
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=95)
    return buf.getvalue()


def download_image(url):
    """下载图片，返回 (bytes, filename)"""
    # 处理 imgproxy URL：提取原始 URL
    if url.startswith('/imgproxy?url='):
        original_url = url.split('/imgproxy?url=', 1)[1]
        original_url = unquote(original_url)
    else:
        original_url = url

    # 从 URL 提取文件名
    parsed = urlparse(original_url)
    path = parsed.path
    filename = os.path.basename(path) or 'image.jpg'
    if '.' not in filename:
        filename += '.jpg'

    # 设置请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    # bohaishibei 和 alicdn 需要 Referer
    if 'bohaishibei' in original_url or 'alicdn' in original_url:
        headers['Referer'] = 'https://www.bohaishibei.com/'
    # itc.cn 图片
    if 'itc.cn' in original_url:
        headers['Referer'] = 'https://www.sohu.com/'

    resp = dl_session.get(original_url, headers=headers, timeout=60)
    resp.raise_for_status()
    image_data = resp.content

    # WebP 转 JPEG（微信不支持 WebP）
    lower_fn = filename.lower()
    if lower_fn.endswith('.webp'):
        try:
            image_data = convert_webp_to_jpeg(image_data)
            filename = filename.replace('.webp', '.jpg')
            print(f'      (WebP -> JPEG conversion: {len(image_data)} bytes)')
        except Exception as e:
            print(f'      (WebP conversion failed: {e})')

    return image_data, filename


def upload_content_image(access_token, image_data, filename):
    """上传文章内图片到微信（uploadimg），返回微信图片URL"""
    url = f'{WECHAT_API}/media/uploadimg'
    params = {'access_token': access_token}

    # 确定 MIME type
    ext = os.path.splitext(filename)[1].lower()
    mime_map = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/jpeg',  # WebP 作为 JPEG 上传
        '.bmp': 'image/bmp',
    }
    mime = mime_map.get(ext, 'image/jpeg')

    # 如果是 webp，修改扩展名为 jpg
    upload_filename = filename
    if ext == '.webp':
        upload_filename = filename.replace('.webp', '.jpg')

    files = {'media': (upload_filename, image_data, mime)}
    resp = session.post(url, params=params, files=files, timeout=60)
    data = resp.json()
    if 'url' in data:
        return data['url']
    else:
        print(f'    上传图片失败: {json.dumps(data, ensure_ascii=False)}')
        return None


def upload_thumb_image(access_token, image_data, filename):
    """上传封面图作为永久素材（add_material），返回 media_id"""
    url = f'{WECHAT_API}/material/add_material'
    params = {'access_token': access_token, 'type': 'image'}

    ext = os.path.splitext(filename)[1].lower()
    mime_map = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/jpeg',
        '.bmp': 'image/bmp',
    }
    mime = mime_map.get(ext, 'image/jpeg')

    upload_filename = filename
    if ext == '.webp':
        upload_filename = filename.replace('.webp', '.jpg')

    files = {'media': (upload_filename, image_data, mime)}
    resp = session.post(url, params=params, files=files, timeout=60)
    data = resp.json()
    if 'media_id' in data:
        return data['media_id']
    else:
        print(f'    上传封面图失败: {json.dumps(data, ensure_ascii=False)}')
        return None


def process_article_images(access_token, article):
    """处理文章中的所有图片：下载 -> 上传到微信 -> 替换URL"""
    content = article['content_html']
    thumb_media_id = None

    # === 步骤A: 上传封面图作为 thumb ===
    if article['image_url']:
        print(f'[3] 上传封面图 (thumb)...')
        try:
            img_data, fname = download_image(article['image_url'])
            print(f'    下载封面图: {fname} ({len(img_data)} bytes)')
            thumb_media_id = upload_thumb_image(access_token, img_data, fname)
            if thumb_media_id:
                print(f'    thumb_media_id: {thumb_media_id}')
        except Exception as e:
            print(f'    封面图上传失败: {e}')

    if not thumb_media_id:
        print('    封面图上传失败，将使用第一张内容图作为封面')
        # 后面处理内容图时取第一张作为 thumb

    # === 步骤B: 处理正文中的所有图片 ===
    print(f'[4] 处理正文图片...')

    # 匹配所有 img src
    img_pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
    matches = list(img_pattern.finditer(content))
    print(f'    找到 {len(matches)} 张图片')

    url_map = {}  # original_url -> wechat_url
    success_count = 0
    fail_count = 0

    for i, match in enumerate(matches):
        original_url = match.group(1)
        # 跳过已经是微信URL的
        if 'mmbiz.qpic.cn' in original_url:
            continue
        # 跳过已处理的
        if original_url in url_map:
            continue

        try:
            img_data, fname = download_image(original_url)
            wechat_url = upload_content_image(access_token, img_data, fname)
            if wechat_url:
                url_map[original_url] = wechat_url
                success_count += 1
                # 如果还没有 thumb，用第一张成功上传的内容图
                if not thumb_media_id:
                    thumb_media_id_result = upload_thumb_image(access_token, img_data, fname)
                    if thumb_media_id_result:
                        thumb_media_id = thumb_media_id_result
                        print(f'    使用第一张内容图作为 thumb: {thumb_media_id}')
                print(f'    [{i+1}/{len(matches)}] OK: {original_url[:60]}... -> {wechat_url[:60]}...')
            else:
                fail_count += 1
                print(f'    [{i+1}/{len(matches)}] FAIL: {original_url[:80]}')
        except Exception as e:
            fail_count += 1
            print(f'    [{i+1}/{len(matches)}] ERROR: {original_url[:60]}... -> {e}')

        # 避免请求过快
        time.sleep(0.3)

    print(f'    完成: 成功 {success_count}, 失败 {fail_count}')

    # === 步骤C: 替换正文中的图片URL ===
    print('[5] 替换正文图片URL...')
    for original_url, wechat_url in url_map.items():
        content = content.replace(original_url, wechat_url)

    # 移除 imgproxy 前缀的残留路径
    # 如果有图片URL没被替换（上传失败的），移除 imgproxy 包装，改为直接链接
    # 这样在微信中至少能看到图片链接
    remaining_imgproxy = re.findall(r'/imgproxy\?url=(https?://[^\s"\'<>]+)', content)
    for orig_url in remaining_imgproxy:
        decoded = unquote(orig_url)
        content = content.replace(f'/imgproxy?url={orig_url}', decoded)
        content = content.replace(f'/imgproxy?url={unquote(orig_url)}', decoded)

    article['content_html'] = content
    article['thumb_media_id'] = thumb_media_id

    return article


def truncate_to_bytes(text, max_bytes=120):
    """截断文本使其 UTF-8 编码不超过 max_bytes 字节"""
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text
    # 逐步截断，避免截断多字节字符
    truncated = encoded[:max_bytes]
    # 尝试解码，如果失败则退一个字节
    while truncated:
        try:
            return truncated.decode('utf-8')
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ''


def create_draft(access_token, article):
    """调用 draft/add 创建草稿"""
    print('[6] 创建微信公众号草稿...')

    # 构建摘要（微信限制120字节）
    digest = truncate_to_bytes(article['excerpt'], 120) if article['excerpt'] else truncate_to_bytes(article['title'], 120)

    # 构建文章数据
    article_data = {
        'title': article['title'][:64],  # 微信限制64字
        'author': truncate_to_bytes(article['author'], 8) if article['author'] else '每日拾趣',
        'digest': digest,
        'content': article['content_html'],
        'content_source_url': f'http://120.55.126.106/post/{article["post_id"]}',
        'thumb_media_id': article.get('thumb_media_id', ''),
        'need_open_comment': 0,
        'only_fans_can_comment': 0,
    }

    print(f'    标题: {article_data["title"]}')
    print(f'    作者: {article_data["author"]}')
    print(f'    摘要: {article_data["digest"][:50]}...')
    print(f'    正文长度: {len(article_data["content"])} 字符')
    print(f'    thumb_media_id: {article_data["thumb_media_id"]}')

    url = f'{WECHAT_API}/draft/add'
    params = {'access_token': access_token}
    payload = {'articles': [article_data]}

    # 手动序列化 JSON，确保中文字符不被转义为 \uXXXX
    json_str = json.dumps(payload, ensure_ascii=False)
    resp = session.post(url, params=params, data=json_str.encode('utf-8'),
                        headers={'Content-Type': 'application/json; charset=utf-8'}, timeout=30)
    data = resp.json()

    if 'media_id' in data:
        print(f'\n=== 草稿创建成功! ===')
        print(f'    草稿 media_id: {data["media_id"]}')
        print(f'    请前往微信公众号后台 -> 草稿箱 查看并发布')
        return data
    else:
        print(f'\n=== 草稿创建失败 ===')
        print(f'    错误信息: {json.dumps(data, ensure_ascii=False)}')
        return data


def main(post_id=None):
    print('=' * 60)
    print('  微信公众号草稿创建工具')
    print('  来源: 每日拾趣 (http://120.55.126.106/)')
    print('=' * 60)
    print()

    # Step 1: 获取 access_token
    access_token = get_access_token()
    print()

    # Step 2: 获取文章
    article = get_article(post_id)
    print()

    # Step 3-5: 处理图片
    article = process_article_images(access_token, article)
    print()

    # Step 6: 创建草稿
    result = create_draft(access_token, article)
    print()

    return result


if __name__ == '__main__':
    import sys
    pid = None
    if len(sys.argv) > 1:
        pid = int(sys.argv[1])
    main(pid)
