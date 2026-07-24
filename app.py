from flask import Flask, render_template, request, Response
import sqlite3
import os
import requests as req_lib

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    conn = get_db()
    c = conn.cursor()

    # Get filter parameters
    category = request.args.get('category', '')
    date_filter = request.args.get('date', '')

    # Build query
    query = 'SELECT * FROM posts WHERE 1=1'
    params = []

    if category:
        query += ' AND category_slug = ?'
        params.append(category)

    if date_filter:
        query += ' AND date LIKE ?'
        params.append(f'%{date_filter}%')

    query += ' ORDER BY post_id DESC'

    posts = c.execute(query, params).fetchall()

    # Get categories for sidebar
    categories = c.execute('SELECT DISTINCT category, category_slug FROM posts ORDER BY category').fetchall()

    # Get dates for sidebar
    dates = c.execute('SELECT DISTINCT date FROM posts ORDER BY date DESC').fetchall()

    conn.close()

    return render_template('index.html', posts=posts, categories=categories,
                           dates=dates, current_category=category,
                           current_date=date_filter)

@app.route('/post/<int:post_id>')
def post(post_id):
    conn = get_db()
    c = conn.cursor()
    article = c.execute('SELECT * FROM posts WHERE post_id = ?', (post_id,)).fetchone()

    # Get adjacent posts
    prev_post = c.execute('SELECT post_id, title FROM posts WHERE post_id < ? ORDER BY post_id DESC LIMIT 1', (post_id,)).fetchone()
    next_post = c.execute('SELECT post_id, title FROM posts WHERE post_id > ? ORDER BY post_id ASC LIMIT 1', (post_id,)).fetchone()

    # Get all images for this post from scrape results (for 潮汐文萃 and video posts)
    images = []
    try:
        import json
        scrape_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scrape_results.json')
        if os.path.exists(scrape_path):
            with open(scrape_path, 'r', encoding='utf-8') as f:
                scrape_data = json.load(f)
            pid = str(post_id)
            if pid in scrape_data:
                all_imgs = scrape_data[pid]['images']
                # Filter out logos/icons/payment gifs
                images = [img for img in all_imgs
                          if 'wechat.gif' not in img and 'alipay.gif' not in img
                          and 'wp-content' not in img
                          and 'avatar' not in img and 'logo' not in img]
    except Exception:
        pass

    conn.close()

    if article is None:
        return render_template('404.html'), 404

    return render_template('post.html', article=article, prev_post=prev_post,
                           next_post=next_post, images=images)

@app.route('/imgproxy')
def imgproxy():
    """Image proxy: fetch images from original CDN with correct Referer header."""
    url = request.args.get('url', '')
    if not url:
        return Response(status=400)

    # Only allow specific domains to prevent abuse
    allowed_domains = ['assets.bohaishibei.com', 'img.bohaishibei.com',
                       'gw.alicdn.com', 'img.alicdn.com',
                       'cdn.bohaishibei.com']
    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain_allowed = any(d in parsed.hostname for d in allowed_domains)
    if not domain_allowed:
        return Response(status=403)

    try:
        headers = {
            'Referer': 'https://www.bohaishibei.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        # Download the full image content (not streaming, to ensure complete data)
        resp = req_lib.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        
        # Determine content type
        content_type = resp.headers.get('Content-Type', '')
        if not content_type.startswith('image/'):
            # Try to detect from URL extension
            if '.gif' in url:
                content_type = 'image/gif'
            elif '.jpg' in url or '.jpeg' in url:
                content_type = 'image/jpeg'
            elif '.png' in url:
                content_type = 'image/png'
            elif '.webp' in url:
                content_type = 'image/webp'
            else:
                content_type = 'application/octet-stream'
        
        # Return the image data with caching headers
        response = Response(resp.content, content_type=content_type)
        response.headers['Cache-Control'] = 'public, max-age=86400'
        response.headers['Content-Length'] = len(resp.content)
        return response
    except Exception as e:
        print(f'Image proxy error for {url}: {e}')
        return Response(status=502)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=False)
