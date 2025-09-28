## 说明
基于[yuque_convert](https://github.com/Y0n9zh/yuque_convert/tree/main)（无法正常下载到本地）修改，用于解决语雀防盗链导致转markdown无法正常加载的问题

## 简介
  由于语雀转markdown有防盗链，导致直接推送到自己的静态博客会无法加载图片，这里将语雀的图片下载到本地images目录下，并替换链接，保持链接中的文件名不变，然后直接上传自己的oss服务器就行。
## 用法
md_img_sync.py

用法:
    python md_img_sync.py

脚本会：
- 读取输入的 markdown 文件（支持通配符，如 'notes' 会匹配 notes.md）
- 提取所有远程图片链接（支持 ![](...) 与普通 ](...) 包含 png/jpg/gif）
- 下载到 ./images/（带重试、校验、临时 .part 文件）
- 生成新的 markdown 文件（原名 + _new.md），把原始远程 URL 替换为你输入的 ServerPath + basename

## 示例
<img width="1510" height="411" alt="QQ_1759028941269" src="https://github.com/user-attachments/assets/732aa308-beab-4af1-bbda-523d04add7ab" />
<img width="1239" height="388" alt="QQ_1759028405131" src="https://github.com/user-attachments/assets/60acb880-1096-44a5-8abf-b2286dee48f8" />
<img width="1814" height="1041" alt="QQ_1759028450832" src="https://github.com/user-attachments/assets/7863fe48-f61b-458f-9495-c2d178880dbb" />


