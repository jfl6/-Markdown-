#!/usr/bin/env python3
# coding: utf-8

"""
md_img_sync.py

用法:
    python md_img_sync.py

脚本会：
- 读取输入的 markdown 文件（支持通配符，如 'notes' 会匹配 notes.md）
- 提取所有远程图片链接（支持 ![](...) 与普通 ](...) 包含 png/jpg/gif）
- 下载到 ./images/（带重试、校验、临时 .part 文件）
- 生成新的 markdown 文件（原名 + _new.md），把原始远程 URL 替换为你输入的 ServerPath + basename
"""

import os
import re
import glob
import requests
from urllib.parse import urlsplit, urljoin, unquote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------- 配置 ----------
IMAGES_DIR = "images"
CHUNK_SIZE = 1024 * 32
MAX_RETRIES = 3
BACKOFF_FACTOR = 0.5
TIMEOUT = 10  # seconds for requests
# -------------------------

def ensure_dir(path):
    """确保目录存在，返回目录路径"""
    os.makedirs(path, exist_ok=True)
    return path

def normalize_server_path(server_path):
    """保证服务器路径以 / 结尾"""
    if not server_path:
        return ""
    if not server_path.endswith('/'):
        server_path += '/'
    return server_path

def extract_image_urls(md_file):
    """
    从 markdown 文件中提取图片/链接 URL 列表（只包含 http/https 且扩展名为常见图片格式）
    支持:
      ![alt](https://.../a.png#...?x=1)
      [text](https://.../a.png#...)
      inline links that end with png/jpg/jpeg/gif
    返回去重后的列表，保持原顺序。
    """
    urls = []
    seen = set()
    # 匹配 markdown 链接或图片的 URL 部分
    pattern = re.compile(r'\]\(\s*(https?://[^\s\)]+?\.(?:png|jpe?g|gif))(?:[^\)]*)\)', re.IGNORECASE)
    with open(md_file, 'r', encoding='utf-8') as f:
        text = f.read()
    for m in pattern.finditer(text):
        raw = m.group(1)
        # 去掉 fragment（#...）和末尾多余 param（保留 query? 有时会影响内容，但我们把文件名去掉 query）
        if raw not in seen:
            urls.append(raw)
            seen.add(raw)
    return urls

def safe_basename_from_url(url):
    """
    从 URL 提取一个安全的文件名（去掉 fragment、query，解码 percent-encoding）
    如果没有可用 basename，则生成一个基于 hash 的名字（极少见）
    """
    u = urlsplit(url)
    path = u.path or ""
    name = os.path.basename(path)
    name = unquote(name)
    # 仍然可能为空，比如 URL 以 / 结尾；尝试从 netloc+path 生成
    if not name:
        # use host + path hashed
        import hashlib
        h = hashlib.sha1(url.encode()).hexdigest()[:12]
        # try to guess extension from path or default to .img
        ext = ".img"
        ext_match = re.search(r'\.(png|jpe?g|gif)$', path, re.IGNORECASE)
        if ext_match:
            ext = "." + ext_match.group(1).lower()
        name = f"{u.netloc.replace(':', '_')}_{h}{ext}"
    # remove any characters that are problematic on Windows/Unix filenames
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    return name

def create_session(max_retries=MAX_RETRIES, backoff_factor=BACKOFF_FACTOR):
    session = requests.Session()
    retries = Retry(
        total=max_retries,
        read=max_retries,
        connect=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(['GET','HEAD','POST','PUT','OPTIONS'])
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    # set a reasonable user agent
    session.headers.update({
        "User-Agent": "md-img-sync/1.0 (+https://example.com)"
    })
    return session

def download_with_resume(session, url, dest_dir, timeout=TIMEOUT):
    """
    下载单个文件到 dest_dir。具备：
    - HEAD 检查 Content-Length（若本地文件存在且大小相等则跳过）
    - 流式写入到 .part 临时文件，成功后原子重命名
    - 捕获异常并返回 (True, msg) 或 (False, msg)
    """
    try:
        safe_name = safe_basename_from_url(url)
        dest_path = os.path.join(dest_dir, safe_name)
        temp_path = dest_path + ".part"

        # HEAD 尝试读取 content-length（有些服务器不支持）
        content_length = None
        try:
            head = session.head(url, allow_redirects=True, timeout=timeout)
            if head.status_code == 200:
                cl = head.headers.get("Content-Length")
                if cl and cl.isdigit():
                    content_length = int(cl)
        except Exception:
            # ignore HEAD 错误，继续用 GET
            content_length = None

        # 如果文件已存在且 content_length 可得且相等，就跳过
        if os.path.exists(dest_path) and content_length is not None:
            local_size = os.path.getsize(dest_path)
            if local_size == content_length:
                return True, f"skip (exists, size match): {safe_name}"

        # 开始下载（GET stream）
        with session.get(url, stream=True, timeout=timeout, allow_redirects=True) as r:
            if r.status_code != 200:
                return False, f"HTTP {r.status_code} for {url}"
            # 如果 HEAD 未给出 content_length，尝试从 GET headers 读
            if content_length is None:
                cl = r.headers.get("Content-Length")
                if cl and cl.isdigit():
                    content_length = int(cl)

            ensure_dir(dest_dir)
            # 写入到临时文件
            with open(temp_path, 'wb') as wf:
                total = 0
                for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        wf.write(chunk)
                        total += len(chunk)
                wf.flush()
            # 校验长度（如果服务器提供）
            if content_length is not None:
                if total != content_length:
                    # 删除损坏的临时文件
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                    return False, f"incomplete download (got {total} != expected {content_length}) for {safe_name}"
            # 原子重命名
            os.replace(temp_path, dest_path)
            return True, f"downloaded: {safe_name}"
    except Exception as e:
        # 保证不抛出到上层，返回 False
        return False, f"exception: {e} for url {url}"

def img_download_batch(img_list, images_dir=IMAGES_DIR):
    """
    使用带重试的 session 下载 img_list
    返回结果列表 (url, success_bool, message)
    """
    results = []
    session = create_session()
    for url in img_list:
        # 清理 url 的 fragment（#）部分用于请求，因为 fragment 不会被发送到服务器，
        # 但在一些 markdown 中可能存在 png#something 导致原始脚本错判
        url_for_req = url.split('#')[0].strip()
        success, msg = download_with_resume(session, url_for_req, images_dir)
        results.append((url, success, msg))
        print(f"[{'OK' if success else 'FAIL'}] {msg}")
    return results

def new_md(md_file, new_file, server_path):
    """
    生成新的 md 文件，将 markdown 中所有远程图片 URL 替换为 server_path + basename
    说明：
      - server_path 会以 / 结尾（normalize_server_path）
      - 只替换匹配到的图片 URL
    """
    server_path = normalize_server_path(server_path)
    with open(md_file, 'r', encoding='utf-8') as f:
        text = f.read()

    # 匹配相同的 pattern（与 extract_image_urls 使用一致），并进行替换
    def repl(m):
        orig_url = m.group(1)
        basename = safe_basename_from_url(orig_url)
        new_url = server_path + basename
        # 保持括号结束
        return m.group(0).replace(orig_url, new_url)

    pattern = re.compile(r'\]\(\s*(https?://[^\s\)]+?\.(?:png|jpe?g|gif))(?:[^\)]*)\)', re.IGNORECASE)
    new_text = pattern.sub(repl, text)

    # 写入新文件（覆盖）
    with open(new_file, 'w', encoding='utf-8') as f:
        f.write(new_text)
    print(f"new markdown written: {new_file}")

def main():
    filename = input("请输入文件名（不含扩展名，支持通配符，如 notes）：").strip()
    if not filename:
        print("未输入文件名，退出。")
        return
    # 支持用户直接输入 *.md 或不带 .md
    if filename.endswith(".md"):
        pattern = filename
    else:
        pattern = filename + ".md"
    mdfile_list = glob.glob(pattern)
    if not mdfile_list:
        print("未找到匹配的 md 文件。")
        return
    server_path = input("请输入服务器路径（OSS or WebServer，示例 https://cdn.example.com/dir/ ）：").strip()
    server_path = normalize_server_path(server_path)

    ensure_dir(IMAGES_DIR)

    for md_file in mdfile_list:
        print(f"处理文件: {md_file}")
        imgs = extract_image_urls(md_file)
        if not imgs:
            print("未发现图片链接，跳过下载。")
        else:
            print(f"发现 {len(imgs)} 个图片链接，开始下载...")
            img_download_batch(imgs, IMAGES_DIR)
        new_file = os.path.splitext(md_file)[0] + "_new.md"
        new_md(md_file, new_file, server_path)
    print("全部完成。")

if __name__ == "__main__":
    main()
