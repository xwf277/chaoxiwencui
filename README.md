# chaoxiwencui (潮汐文萃)

基于 Flask + SQLite 的内容资讯网站，克隆自 bohaishibei.com 的内容架构。

## 功能特性

- 文章列表展示，支持分类筛选
- 文章详情页，支持内嵌图片和视频
- CDN 图片防盗链代理 (imgproxy)
- 响应式设计，适配移动端

## 技术栈

- Python 3.13 + Flask
- SQLite 数据库
- Gunicorn + Nginx (生产部署)
- Jinja2 模板引擎

## 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 初始化数据库
python init_db.py

# 启动服务
python app.py
# 访问 http://127.0.0.1:8080
```

## 生产部署

```bash
# 使用 Gunicorn
gunicorn -w 2 -b 127.0.0.1:8080 app:app

# Nginx 反向代理配置参考
# proxy_pass http://127.0.0.1:8080;
```

## 项目结构

```
bohaishibei/
├── app.py                  # Flask 主应用
├── init_db.py             # 数据库初始化
├── scrape_html_content.py  # HTML 内容抓取
├── scrape_images.py       # 图片/视频 URL 抓取
├── scrape_results.json     # 抓取结果
├── database.db            # SQLite 数据库
├── requirements.txt       # Python 依赖
├── static/
│   └── style.css          # 样式表
└── templates/
    ├── index.html         # 首页模板
    ├── post.html          # 文章详情模板
    └── 404.html           # 404 页面
```

## 数据来源

内容抓取自 bohaishibei.com，图片通过 /imgproxy 代理绕过 CDN 防盗链。
