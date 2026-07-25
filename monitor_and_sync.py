#!/usr/bin/env python3
"""
Monitor bohaishibei.com for new articles and sync to our website.

Features:
- Scrape homepage (page 1 & 2) for new article links
- Skip "优惠" (category-tao) articles
- For each new article, scrape full content (HTML + text + images + videos)
- Update local database
- Deploy updated database to cloud server (120.55.126.106)
- At 21:40 run (hour == 21), also push to GitHub

Usage:
    python monitor_and_sync.py
"""
import sqlite3
import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
import paramiko
from datetime import datetime
import subprocess
import sys

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'database.db')
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.json')
SCRAPE_RESULTS_PATH = os.path.join(SCRIPT_DIR, 'scrape_results.json')

# HTTP headers for scraping
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://www.bohaishibei.com/',
}


def load_config():
    """Load configuration from config.json."""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_existing_post_ids():
    """Get all post_ids already in our database."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT post_id FROM posts')
    ids = set(row[0] for row in cur.fetchall())
    conn.close()
    return ids


def scrape_homepage_pages(config):
    """
    Scrape homepage page 1 and 2 to find all recent articles.
    Returns list of dicts: {post_id, category_slug, category_name, title, date}
    """
    base_url = config['source_site']
    skip_cats = set(config.get('skip_categories', ['tao']))
    cat_map = config.get('category_mapping', {})

    session = requests.Session()
    session.headers.update(HEADERS)

    all_articles = []
    seen_ids = set()

    for page_num in [1, 2]:
        url = base_url if page_num == 1 else f'{base_url}/page/{page_num}/'
        try:
            resp = session.get(url, timeout=30)
            resp.encoding = 'utf-8'
            if resp.status_code != 200:
                print(f'  Page {page_num}: HTTP {resp.status_code}, skipping')
                continue
        except Exception as e:
            print(f'  Page {page_num}: Error - {e}')
            continue

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Find all article divs
        articles = soup.find_all('div', class_=re.compile(r'post.*type-post|type-post.*post'))

        for art in articles:
            classes = art.get('class', [])
            post_id = None
            cat_slug = None

            for cls in classes:
                if cls.startswith('post-') and cls[5:].isdigit():
                    post_id = int(cls[5:])
                if cls.startswith('category-'):
                    cat_slug = cls[9:]

            if post_id is None or post_id in seen_ids:
                continue
            if cat_slug is None:
                continue

            seen_ids.add(post_id)

            # Skip 优惠 category
            if cat_slug in skip_cats:
                continue

            # Get category name from mapping
            cat_info = cat_map.get(cat_slug, {'name': cat_slug, 'slug': cat_slug})
            category_name = cat_info['name']
            category_slug = cat_info['slug']

            # Get title from the post link
            title_link = art.find('a', href=True)
            title = title_link.get_text(strip=True) if title_link else ''
            # Clean up title (remove category/date prefix if present)
            if title:
                # Remove leading category text like "文摘•" or "博海拾贝•"
                title = re.sub(r'^(文摘|博海拾贝|视频|优惠)[•·]', '', title).strip()
                # Replace 博海拾贝 in title with 每日拾趣
                title = title.replace('博海拾贝', '每日拾趣')

            # Get date
            date_span = art.find('span', class_='entry-date')
            date_text = ''
            if date_span:
                date_text = date_span.get_text(strip=True)
                # Extract date pattern like "2026 年 7 月 25 日"
                date_match = re.search(r'(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)', date_text)
                if date_match:
                    date_text = date_match.group(1)

            all_articles.append({
                'post_id': post_id,
                'category_name': category_name,
                'category_slug': category_slug,
                'title': title,
                'date': date_text,
            })

        print(f'  Page {page_num}: Found {len(articles)} articles, {len([a for a in all_articles if a["post_id"] in seen_ids])} unique non-优惠')

    return all_articles


def scrape_article_detail(post_id):
    """
    Scrape a single article page for full content.
    Returns dict with: title, date, excerpt, content, content_html, image_url, video_url
    """
    url = f'https://www.bohaishibei.com/post/{post_id}/'

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.encoding = 'utf-8'
        if resp.status_code != 200:
            print(f'    HTTP {resp.status_code}')
            return None
    except Exception as e:
        print(f'    Error: {e}')
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Extract title
    title_elem = soup.find('h1', class_='post-title') or soup.find('h1')
    title = title_elem.text.strip() if title_elem else ''
    # Replace 博海拾贝 in title with 每日拾趣
    title = title.replace('博海拾贝', '每日拾趣')

    # Extract date
    date_span = soup.find('span', class_='entry-date')
    date_text = ''
    if date_span:
        date_text = date_span.get_text(strip=True)
        date_match = re.search(r'(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)', date_text)
        if date_match:
            date_text = date_match.group(1)

    # Find main content div
    content_div = soup.find('div', class_='entry-content')
    if not content_div:
        content_div = soup.find('div', class_='post-content')
    if not content_div:
        content_div = soup.find('article')

    if not content_div:
        print(f'    No content div found')
        return None

    # Extract first image as cover (before modifying)
    first_img_url = ''
    first_img = content_div.find('img')
    if first_img:
        src = first_img.get('src', '')
        if src:
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                src = 'https://www.bohaishibei.com' + src
            first_img_url = src

    # Extract video URL (B站 iframe)
    video_url = ''
    for iframe in content_div.find_all('iframe'):
        src = iframe.get('src', '')
        if 'bilibili' in src or 'player.bilibili' in src:
            video_url = src
            break

    # Also check for Bilibili links in text
    if not video_url:
        for link in content_div.find_all('a', href=True):
            href = link['href']
            if 'bilibili.com/video' in href:
                bv_match = re.search(r'BV[\w]+', href)
                if bv_match:
                    bv_id = bv_match.group()
                    video_url = f'https://player.bilibili.com/player.html?bvid={bv_id}&autoplay=0'
                    break

    # Convert image srcs to proxy URLs
    allowed_prefixes = (
        'https://assets.bohaishibei.com',
        'https://img.bohaishibei.com',
        'https://gw.alicdn.com',
        'https://img.alicdn.com',
        'https://cdn.bohaishibei.com',
    )

    for img in content_div.find_all('img'):
        src = img.get('src', '')
        if src:
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                src = 'https://www.bohaishibei.com' + src

            if src.startswith(allowed_prefixes):
                img['src'] = f'/imgproxy?url={src}'
                img['loading'] = 'lazy'
                if 'width' in img.attrs:
                    del img['width']
                if 'height' in img.attrs:
                    del img['height']
                img['style'] = 'max-width: 100%; height: auto; display: block; margin: 10px auto;'

    # Fix iframe video embeds
    for iframe in content_div.find_all('iframe'):
        src = iframe.get('src', '')
        if 'bilibili' in src or 'player.bilibili' in src:
            iframe['src'] = src
            iframe['allowfullscreen'] = 'true'
            iframe['style'] = 'width: 100%; height: 450px; border: none;'

    # Remove unwanted elements
    for unwanted in content_div.find_all(['button', 'script', 'style']):
        unwanted.decompose()

    for cls_pattern in ['share', 'social', 'pay', 'reward', 'donate', 'ad']:
        for elem in content_div.find_all(class_=re.compile(cls_pattern, re.I)):
            elem.decompose()

    for a in content_div.find_all('a'):
        href = a.get('href', '')
        if 'pay' in href or 'reward' in href or 'donate' in href:
            a.decompose()

    # Get clean HTML
    content_html = str(content_div)
    # Replace 博海拾贝 in content with 每日拾趣
    content_html = content_html.replace('博海拾贝', '每日拾趣')

    # Get plain text content
    content_text = content_div.get_text(separator='\n', strip=True)
    # Clean up excessive newlines
    content_text = re.sub(r'\n{3,}', '\n\n', content_text)
    # Replace 博海拾贝 in content with 每日拾趣
    content_text = content_text.replace('博海拾贝', '每日拾趣')

    # Extract excerpt (first meaningful paragraph, up to 200 chars)
    excerpt = ''
    for p in content_div.find_all('p'):
        text = p.get_text(strip=True)
        if len(text) > 20:  # Skip very short paragraphs
            excerpt = text[:200]
            break
    if not excerpt:
        excerpt = content_text[:200]
    # Replace 博海拾贝 in excerpt with 每日拾趣
    excerpt = excerpt.replace('博海拾贝', '每日拾趣')

    return {
        'title': title,
        'date': date_text,
        'excerpt': excerpt,
        'content': content_text,
        'content_html': content_html,
        'image_url': first_img_url,
        'video_url': video_url,
    }


def insert_post(post_id, title, category, category_slug, date, excerpt,
                content, content_html, image_url, video_url):
    """Insert a new post into the database."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute('''INSERT OR IGNORE INTO posts
            (post_id, title, category, category_slug, date, excerpt, content,
             content_html, author, image_url, video_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (post_id, title, category, category_slug, date, excerpt, content,
               content_html, '许文锋', image_url, video_url))
        conn.commit()
        print(f'    Inserted: {title[:50]}')
    except sqlite3.IntegrityError:
        print(f'    Already exists: {post_id}')
    conn.close()


def update_scrape_results(post_id, detail):
    """Update scrape_results.json with new post data (for app.py images feature)."""
    results = {}
    if os.path.exists(SCRAPE_RESULTS_PATH):
        try:
            with open(SCRAPE_RESULTS_PATH, 'r', encoding='utf-8') as f:
                results = json.load(f)
        except Exception:
            results = {}

    # Extract image URLs from content_html
    images = []
    for img_match in re.finditer(r'src="/imgproxy\?url=(.*?)"', detail['content_html']):
        img_url = img_match.group(1)
        if img_url not in images:
            images.append(img_url)

    videos = [detail['video_url']] if detail['video_url'] else []

    results[str(post_id)] = {
        'images': images,
        'videos': videos,
        'content_html': detail['content_html'],
        'title': detail['title'],
    }

    with open(SCRAPE_RESULTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def deploy_to_cloud(config):
    """Deploy updated database and scrape_results to cloud server."""
    server = config['server']
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(server['host'], username=server['user'], password=server['password'])

    sftp = ssh.open_sftp()

    # Upload database.db
    local_db = os.path.join(SCRIPT_DIR, 'database.db')
    remote_db = server['remote_db']
    sftp.put(local_db, remote_db)
    print(f'  Uploaded: {remote_db}')

    # Upload scrape_results.json
    local_results = os.path.join(SCRIPT_DIR, 'scrape_results.json')
    remote_results = f"{server['path']}/scrape_results.json"
    if os.path.exists(local_results):
        sftp.put(local_results, remote_results)
        print(f'  Uploaded: {remote_results}')

    sftp.close()

    # Restart the service
    stdin, stdout, stderr = ssh.exec_command('systemctl restart chaoxiwencai')
    err = stderr.read().decode().strip()
    if err:
        print(f'  Restart stderr: {err}')

    # Verify service status
    stdin, stdout, stderr = ssh.exec_command('systemctl is-active chaoxiwencai')
    status = stdout.read().decode().strip()
    print(f'  Service status: {status}')

    ssh.close()
    return status == 'active'


def push_to_github(config):
    """Push updated files to GitHub."""
    gh = config['github']
    token = gh['token']
    repo = gh['repo']
    branch = gh.get('branch', 'main')

    # Git add, commit, and push
    os.chdir(SCRIPT_DIR)

    # Add changed files
    subprocess.run(['git', 'add', 'database.db', 'scrape_results.json'],
                   capture_output=True, cwd=SCRIPT_DIR)

    # Check if there are changes to commit
    result = subprocess.run(['git', 'diff', '--cached', '--name-only'],
                           capture_output=True, text=True, cwd=SCRIPT_DIR)
    changed_files = result.stdout.strip()

    if not changed_files:
        print('  No changes to push to GitHub')
        return True

    # Commit
    today = datetime.now().strftime('%Y-%m-%d')
    commit_msg = f'Daily sync: {today} - content update from monitoring'

    subprocess.run(['git', 'commit', '-m', commit_msg],
                   capture_output=True, cwd=SCRIPT_DIR)

    # Push using token in URL (force to handle any divergence)
    push_url = f'https://xwf277:{token}@github.com/{repo}.git'
    result = subprocess.run(['git', 'push', push_url, branch, '--force'],
                           capture_output=True, text=True, cwd=SCRIPT_DIR)

    if result.returncode == 0:
        print(f'  GitHub push successful: {commit_msg}')
        return True
    else:
        print(f'  GitHub push failed: {result.stderr}')
        # Fallback: try GitHub Contents API for database.db
        try:
            push_via_github_api(config, commit_msg)
            print(f'  GitHub API fallback successful')
            return True
        except Exception as api_err:
            print(f'  GitHub API fallback failed: {api_err}')
            return False


def push_via_github_api(config, commit_msg):
    """Fallback: push database.db via GitHub Contents API when git push fails."""
    import urllib.request
    import base64

    gh = config['github']
    token = gh['token']
    owner, repo = gh['repo'].split('/')

    for filepath in ['database.db', 'scrape_results.json']:
        local_path = os.path.join(SCRIPT_DIR, filepath)
        if not os.path.exists(local_path):
            continue

        # Get current SHA (required for update)
        api_url = f'https://api.github.com/repos/{owner}/{repo}/contents/{filepath}'
        req = urllib.request.Request(api_url,
            headers={'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'})
        try:
            resp = urllib.request.urlopen(req)
            data = json.loads(resp.read())
            sha = data['sha']
        except urllib.error.HTTPError as e:
            if e.code == 404:
                sha = None  # File doesn't exist yet
            else:
                raise

        # Read and encode file
        with open(local_path, 'rb') as f:
            content_b64 = base64.b64encode(f.read()).decode('utf-8')

        # Update via API
        update_data = json.dumps({
            'message': commit_msg,
            'content': content_b64,
            'sha': sha
        }).encode('utf-8')

        req2 = urllib.request.Request(api_url, data=update_data,
            headers={'Authorization': f'token {token}', 'Content-Type': 'application/json',
                     'Accept': 'application/vnd.github.v3+json'},
            method='PUT')
        urllib.request.urlopen(req2)
        print(f'  API updated: {filepath}')


def main():
    print(f'{"="*60}')
    print(f'每日拾趣 - 网站监控同步')
    print(f'时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'{"="*60}')

    config = load_config()

    # Step 1: Get existing post IDs
    existing_ids = get_existing_post_ids()
    print(f'\n[1] 数据库现有文章: {len(existing_ids)} 篇')

    # Step 2: Scrape homepage for articles
    print(f'\n[2] 抓取博海拾贝首页...')
    homepage_articles = scrape_homepage_pages(config)
    print(f'  共发现 {len(homepage_articles)} 篇非优惠文章')

    # Step 3: Find new articles
    new_articles = [a for a in homepage_articles if a['post_id'] not in existing_ids]
    print(f'\n[3] 新文章: {len(new_articles)} 篇')

    if not new_articles:
        print('  没有新文章，跳过更新')
    else:
        # Step 4: Scrape each new article
        print(f'\n[4] 抓取新文章详情...')
        for i, article in enumerate(new_articles):
            print(f'  [{i+1}/{len(new_articles)}] Post {article["post_id"]}: {article["title"][:40]}')
            detail = scrape_article_detail(article['post_id'])

            if detail is None:
                print(f'    跳过: 无法抓取')
                continue

            # Use homepage data for category/title/date if article page didn't provide them
            title = detail['title'] or article['title']
            date = detail['date'] or article['date']

            insert_post(
                post_id=article['post_id'],
                title=title,
                category=article['category_name'],
                category_slug=article['category_slug'],
                date=date,
                excerpt=detail['excerpt'],
                content=detail['content'],
                content_html=detail['content_html'],
                image_url=detail['image_url'],
                video_url=detail['video_url'],
            )

            # Update scrape_results.json
            update_scrape_results(article['post_id'], detail)

            # Rate limit
            if i < len(new_articles) - 1:
                time.sleep(1)

    # Step 5: Deploy to cloud server
    print(f'\n[5] 部署到云服务器 ({config["server"]["host"]})...')
    try:
        success = deploy_to_cloud(config)
        if success:
            print('  云服务器部署成功')
        else:
            print('  云服务器部署失败')
    except Exception as e:
        print(f'  云服务器部署错误: {e}')

    # Step 6: Check if it's the 21:40 run (push to GitHub)
    current_hour = datetime.now().hour
    if current_hour == 21:
        print(f'\n[6] 当前为 {current_hour}:40，推送到 GitHub...')
        try:
            push_to_github(config)
        except Exception as e:
            print(f'  GitHub 推送错误: {e}')
    else:
        print(f'\n[6] 当前为 {current_hour}:40，非 21:40，跳过 GitHub 推送')

    # Summary
    print(f'\n{"="*60}')
    print(f'监控完成')
    print(f'  新文章: {len(new_articles)} 篇')
    print(f'  云服务器: {"已更新" if new_articles else "无变化"}')
    print(f'  GitHub: {"已推送" if current_hour == 21 else "跳过(非21:40)"}')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()
