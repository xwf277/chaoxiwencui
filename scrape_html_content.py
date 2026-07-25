"""
Re-scrape all posts to get the full HTML content with inline images.
This is critical for 每日拾趣 collection posts where each numbered item 【1】【2】...
is paired with an image/GIF that must be displayed inline.
"""
import sqlite3
import requests
from bs4 import BeautifulSoup
import json
import os
import time
import re
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
SCRAPE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scrape_results.json')

def get_post_ids():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT post_id FROM posts ORDER BY post_id')
    ids = [row[0] for row in cur.fetchall()]
    conn.close()
    return ids

def scrape_post_html(post_id):
    """Scrape the full HTML content of a post, converting image URLs to proxy URLs."""
    url = f'https://www.bohaishibei.com/post/{post_id}/'
    headers = {
        'Referer': 'https://www.bohaishibei.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.encoding = 'utf-8'  # Force UTF-8 encoding for Chinese content
        if resp.status_code != 200:
            print(f'  Failed: HTTP {resp.status_code}')
            return None
    except Exception as e:
        print(f'  Failed: {e}')
        return None
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Find the main content div
    content_div = soup.find('div', class_='entry-content')
    if not content_div:
        content_div = soup.find('div', class_='post-content')
    if not content_div:
        content_div = soup.find('article')
    
    if not content_div:
        print(f'  No content div found')
        return None
    
    # Convert all image src to proxy URLs
    for img in content_div.find_all('img'):
        src = img.get('src', '')
        if src and (src.startswith('https://assets.bohaishibei.com') or
                    src.startswith('https://img.bohaishibei.com') or
                    src.startswith('https://gw.alicdn.com') or
                    src.startswith('https://img.alicdn.com') or
                    src.startswith('https://cdn.bohaishibei.com')):
            img['src'] = f'/imgproxy?url={src}'
            img['loading'] = 'lazy'
            # Remove width/height attributes that might distort display
            if 'width' in img.attrs:
                del img['width']
            if 'height' in img.attrs:
                del img['height']
            img['style'] = 'max-width: 100%; height: auto; display: block; margin: 10px auto;'
    
    # Also fix iframe video embeds
    for iframe in content_div.find_all('iframe'):
        src = iframe.get('src', '')
        if 'bilibili' in src or 'player.bilibili' in src:
            iframe['src'] = src  # B站embeds don't need proxy
            iframe['allowfullscreen'] = 'true'
            iframe['style'] = 'width: 100%; height: 450px; border: none;'
    
    # Remove unwanted elements (payment buttons, share buttons, etc.)
    for unwanted in content_div.find_all(['button', 'script', 'style']):
        unwanted.decompose()
    
    # Remove elements with specific classes (ads, share buttons, etc.)
    for cls_pattern in ['share', 'social', 'pay', 'reward', 'donate', 'ad']:
        for elem in content_div.find_all(class_=re.compile(cls_pattern, re.I)):
            elem.decompose()
    
    # Remove links that are just payment/donation links
    for a in content_div.find_all('a'):
        href = a.get('href', '')
        if 'pay' in href or 'reward' in href or 'donate' in href:
            a.decompose()
    
    # Get the clean HTML
    html_content = str(content_div)
    
    return html_content

def update_database(post_id, html_content):
    """Update the content_html field in the database."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE posts SET content_html = ? WHERE post_id = ?', (html_content, post_id))
    conn.commit()
    conn.close()

def main():
    post_ids = get_post_ids()
    print(f'Found {len(post_ids)} posts to scrape')
    
    success = 0
    fail = 0
    
    for i, post_id in enumerate(post_ids):
        print(f'[{i+1}/{len(post_ids)}] Scraping post {post_id}...')
        html = scrape_post_html(post_id)
        if html:
            update_database(post_id, html)
            # Count images in the HTML
            img_count = html.count('<img')
            gif_count = html.count('.gif')
            print(f'  Success: {len(html)} chars, {img_count} images, {gif_count} GIF references')
            success += 1
        else:
            fail += 1
        
        # Rate limit: small delay between requests
        if i < len(post_ids) - 1:
            time.sleep(0.5)
    
    print(f'\nDone: {success} succeeded, {fail} failed out of {len(post_ids)} posts')

if __name__ == '__main__':
    main()
