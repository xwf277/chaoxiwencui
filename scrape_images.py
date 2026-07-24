"""Scrape image and video URLs from bohaishibei.com articles"""
import requests
from bs4 import BeautifulSoup
import json
import time
import re

BASE_URL = 'https://www.bohaishibei.com/post/{}/'

# All post IDs we have in our database
POST_IDS = [
    111333, 111332, 111331, 111329, 111328, 111327, 111326, 111325,
    111324, 111323, 111322, 111321, 111319, 111318, 111317, 111316,
    111314, 111313, 111309, 111308, 111306, 111305, 111303, 111302,
    111301, 111300, 111297, 111296, 111295, 111291, 111290, 111289,
    111288, 111286, 111285, 111284, 111283, 111281, 111280, 111279,
    111278, 111276, 111275, 111274, 111273, 111272, 111277, 111256,
    111206,
]

results = {}
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

session = requests.Session()
session.headers.update(headers)

for pid in POST_IDS:
    url = BASE_URL.format(pid)
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            print(f"  [FAIL] {pid} - HTTP {resp.status_code}")
            continue
        
        soup = BeautifulSoup(resp.text, 'lxml')
        
        # Extract images from the article content area
        images = []
        # Find the main content area
        content_area = soup.find('div', class_='post-content') or soup.find('article') or soup.find('div', class_='entry-content')
        
        if content_area:
            for img in content_area.find_all('img'):
                src = img.get('src', '')
                if src:
                    # Make sure it's a full URL
                    if src.startswith('//'):
                        src = 'https:' + src
                    elif src.startswith('/'):
                        src = 'https://www.bohaishibei.com' + src
                    images.append(src)
        else:
            # Try finding images in the whole page body
            body = soup.find('div', id='content') or soup.find('main') or soup.body
            if body:
                for img in body.find_all('img'):
                    src = img.get('src', '')
                    data_src = img.get('data-src', '')
                    actual_src = data_src if data_src else src
                    if actual_src:
                        if actual_src.startswith('//'):
                            actual_src = 'https:' + actual_src
                        elif actual_src.startswith('/'):
                            actual_src = 'https://www.bohaishibei.com' + actual_src
                        # Filter out tiny/logos/icons
                        if 'avatar' not in actual_src and 'logo' not in actual_src and 'icon' not in actual_src and 'emoji' not in actual_src:
                            images.append(actual_src)
        
        # Extract video links (Bilibili, iframe embeds, etc.)
        videos = []
        # Check for iframe embeds
        iframes = soup.find_all('iframe')
        for iframe in iframes:
            src = iframe.get('src', '')
            if src and ('bilibili' in src or 'player' in src or 'video' in src):
                videos.append(src)
        
        # Check for Bilibili links in text
        all_links = soup.find_all('a')
        for link in all_links:
            href = link.get('href', '')
            if 'bilibili.com/video' in href:
                # Convert to embed URL
                bv_match = re.search(r'BV[\w]+', href)
                if bv_match:
                    bv_id = bv_match.group()
                    embed_url = f'https://player.bilibili.com/player.html?bvid={bv_id}&autoplay=0'
                    videos.append(embed_url)
        
        # Also check for direct video tags
        video_tags = soup.find_all('video')
        for vt in video_tags:
            src = vt.get('src', '')
            if src:
                videos.append(src)
        
        # Extract the full HTML content of the article
        content_html = ''
        if content_area:
            content_html = str(content_area)
        
        # Also try to find the title
        title_elem = soup.find('h1', class_='post-title') or soup.find('h1') or soup.find('title')
        title = title_elem.text.strip() if title_elem else ''
        
        results[pid] = {
            'images': images,
            'videos': videos,
            'content_html': content_html,
            'title': title,
        }
        
        print(f"  [OK] {pid} - {len(images)} images, {len(videos)} videos")
        
    except Exception as e:
        print(f"  [ERROR] {pid} - {e}")
    
    time.sleep(0.5)  # Be polite

# Save results
with open('D:/workbuddy/bohaishibei/scrape_results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\nDone! Saved results for {len(results)} posts")
print(f"Total images found: {sum(len(r['images']) for r in results.values())}")
print(f"Total videos found: {sum(len(r['videos']) for r in results.values())}")
