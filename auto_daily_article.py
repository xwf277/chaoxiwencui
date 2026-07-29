"""
每日拾趣标准文章自动同步到公众号草稿箱

功能：
1. 仅在 08:00-22:00 活跃窗口内执行（22:00 起休眠）
2. 检查当天是否已成功同步过，避免重复执行
3. 抓取 http://120.55.126.106/?category=main 查找今天标题为 [每日拾趣MMDD]xxx 的文章
4. 通过 wechat_publish.py 创建公众号文章草稿（含文字+图片）
5. 记录成功日期并输出通知文本
"""
import os
import re
import sys
import json
import requests
from datetime import datetime
from urllib.parse import unquote

# 导入现有的微信草稿创建逻辑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish

# ========== 配置 ==========
SITE_URL = 'http://120.55.126.106'
CATEGORY_URL = f'{SITE_URL}/?category=main'
MEMORY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.workbuddy', 'automations', 'automation-1785288318888')
SUCCESS_FILE = os.path.join(MEMORY_DIR, 'last_article_success_date.txt')

ACTIVE_HOUR_START = 8   # 包含
ACTIVE_HOUR_END = 22    # 不包含

session = requests.Session()
session.trust_env = False
session.proxies = {'http': None, 'https': None}


def now_cn():
    return datetime.now()


def today_cn():
    return now_cn().strftime('%Y-%m-%d')


def today_mmdd():
    return now_cn().strftime('%m%d')


def is_active_window():
    """判断是否在 08:00-22:00 活跃窗口内"""
    h = now_cn().hour
    return ACTIVE_HOUR_START <= h < ACTIVE_HOUR_END


def already_succeeded_today():
    """检查今天是否已成功同步过"""
    if not os.path.exists(SUCCESS_FILE):
        return False
    try:
        with open(SUCCESS_FILE, 'r', encoding='utf-8') as f:
            last_date = f.read().strip()
        return last_date == today_cn()
    except Exception:
        return False


def record_success():
    """记录今天已成功同步"""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(SUCCESS_FILE, 'w', encoding='utf-8') as f:
        f.write(today_cn())


def fetch_html(url):
    """抓取网页 HTML"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    resp = session.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    return resp.text


def find_today_article():
    """
    从每日拾趣分类页查找今天发布的文章。
    返回 (post_id, full_title) 或 (None, None)
    """
    html = fetch_html(CATEGORY_URL)
    mmdd = today_mmdd()
    # 匹配：<a href="/post/111470">[每日拾趣0728]不是一家不蹲一窝</a>
    pattern = re.compile(
        r'<a href="/post/(\d+)">\[每日拾趣(' + re.escape(mmdd) + r')\]([^<]+)</a>',
        re.IGNORECASE
    )
    matches = list(pattern.finditer(html))
    if not matches:
        return None, None
    # 取第一个匹配（最新）
    m = matches[0]
    post_id = int(m.group(1))
    title = m.group(3).strip()
    full_title = f'[每日拾趣{mmdd}]{title}'
    return post_id, full_title


def build_notification(success, post_id=None, title=None, media_id=None, error=None):
    """生成通知文本"""
    now = now_cn().strftime('%Y-%m-%d %H:%M:%S')
    if success:
        return (
            f"✅ 每日拾趣文章同步成功\n"
            f"时间：{now}\n"
            f"标题：{title}\n"
            f"文章ID：{post_id}\n"
            f"草稿 media_id：{media_id}\n"
            f"请前往公众号后台草稿箱查看并发布。"
        )
    else:
        return (
            f"⏸ 每日拾趣文章同步未执行\n"
            f"时间：{now}\n"
            f"原因：{error}\n"
            f"下次将在活跃窗口（08:00-22:00）重试。"
        )


def main():
    print('=' * 60)
    print('  每日拾趣标准文章自动同步')
    print(f'  当前时间：{now_cn().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 60)
    print()

    # 1. 休眠窗口检查
    if not is_active_window():
        msg = build_notification(False, error=f'当前不在活跃窗口（{ACTIVE_HOUR_START}:00-{ACTIVE_HOUR_END}:00），任务休眠')
        print(msg)
        return {'success': False, 'reason': 'sleep_window', 'message': msg}

    # 2. 当天去重检查
    if already_succeeded_today():
        msg = build_notification(False, error=f'今日（{today_cn()}）已成功同步过，跳过')
        print(msg)
        return {'success': False, 'reason': 'already_done', 'message': msg}

    # 3. 查找今日文章
    print(f'[1] 查找今日文章（日期标记 {today_mmdd()}）...')
    post_id, title = find_today_article()
    if not post_id:
        msg = build_notification(False, error=f'网站尚未发布今日“每日拾趣{today_mmdd()}”文章')
        print(msg)
        return {'success': False, 'reason': 'not_found', 'message': msg}

    print(f'    找到文章：{title}')
    print(f'    post_id：{post_id}')
    print()

    # 4. 创建公众号草稿（复用 wechat_publish）
    print('[2] 创建微信公众号草稿...')
    try:
        result = wechat_publish.main(post_id)
    except Exception as e:
        msg = build_notification(False, post_id=post_id, title=title, error=f'创建草稿异常：{e}')
        print(msg)
        return {'success': False, 'reason': 'exception', 'message': msg}

    if not isinstance(result, dict) or 'media_id' not in result:
        err = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
        msg = build_notification(False, post_id=post_id, title=title, error=f'创建草稿失败：{err}')
        print(msg)
        return {'success': False, 'reason': 'draft_failed', 'message': msg}

    # 5. 记录成功
    record_success()
    media_id = result['media_id']
    msg = build_notification(True, post_id=post_id, title=title, media_id=media_id)
    print(msg)

    return {'success': True, 'post_id': post_id, 'title': title, 'media_id': media_id, 'message': msg}


if __name__ == '__main__':
    result = main()
    sys.exit(0 if result.get('success') else 0)  # 非致命错误不退出异常，方便定时任务继续
