#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据 Minecraft 正版用户名下载皮肤 PNG，或直接在内存中预览。

用法:
    python mc_skin.py <username>               # 保存为 <username>.png
    python mc_skin.py <username> -o out.png    # 保存到指定路径
    python mc_skin.py <username> -v            # 不保存，直接打开预览窗口
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import requests

LOOKUP_URL = "https://api.minecraftservices.com/minecraft/profile/lookup/name/{name}"
SESSION_URL = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
UA = "mc-skin-downloader/1.0 (python-requests)"


# ---------- 1. 用户名 -> UUID ----------
def get_uuid(username: str) -> str:
    resp = requests.get(
        LOOKUP_URL.format(name=username),
        headers={"User-Agent": UA},
        timeout=10,
    )
    if resp.status_code in (204, 404):
        raise RuntimeError(f"玩家不存在: {username}")
    resp.raise_for_status()
    uuid = resp.json().get("id")
    if not uuid:
        raise RuntimeError(f"接口未返回 UUID: {resp.json()}")
    return uuid


# ---------- 2. UUID -> profile（含 textures 的 Base64） ----------
def get_profile(uuid: str) -> dict:
    resp = requests.get(
        SESSION_URL.format(uuid=uuid),
        params={"unsigned": "false"},
        headers={"User-Agent": UA},
        timeout=10,
    )
    if resp.status_code in (204, 404):
        raise RuntimeError(f"查不到该 UUID 的 profile: {uuid}")
    resp.raise_for_status()
    return resp.json()


def extract_textures_value(profile: dict) -> str:
    for prop in profile.get("properties", []):
        if prop.get("name") == "textures":
            return prop["value"]
    raise RuntimeError("该 profile 中没有 textures 属性")


# ---------- 3. Base64 -> 皮肤链接 ----------
def decode_skin_url(b64_value: str) -> str:
    data = json.loads(base64.b64decode(b64_value).decode("utf-8"))
    return data["textures"]["SKIN"]["url"]


# ---------- 4. 下载皮肤 PNG（只返回 bytes） ----------
def fetch_skin_png(url: str) -> bytes:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=10)
    resp.raise_for_status()
    if not resp.content.startswith(b"\x89PNG"):
        raise RuntimeError("下载内容不是有效 PNG")
    return resp.content


# ---------- 5. 内存预览窗口 ----------
def preview_skin(data: bytes) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("Skin Preview — 滚轮/± 缩放，拖拽平移，Esc 关闭")
    root.geometry("640x480")
    root.minsize(200, 150)

    base = tk.PhotoImage(data=data)

    CHECK = 8                       # 棋盘格边长（像素，细密）
    C1, C2 = "#ffffff", "#d0d0d0"
    BORDER_COLOR = "#000000"
    BORDER_WIDTH = 2

    state = {
        "zoom": 4,
        "img": None,      # 当前缩放的 PhotoImage
        "img_id": None,   # canvas 上的图片 item id
        "border_id": None,# canvas 上的描边 item id
        "off": [0, 0],    # 图片左上角在 canvas 中的坐标
        "dragging": False,
        "last": (0, 0),
    }

    canvas = tk.Canvas(root, bg=C2, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    # ---------- 辅助 ----------
    def csize():
        return canvas.winfo_width(), canvas.winfo_height()

    def draw_checkerboard():
        canvas.delete("bg")
        w, h = csize()
        for y in range(0, h, CHECK):
            for x in range(0, w, CHECK):
                if ((x // CHECK) + (y // CHECK)) % 2 == 0:
                    canvas.create_rectangle(
                        x, y, x + CHECK, y + CHECK,
                        fill=C1, outline="", tags="bg",
                    )
        canvas.tag_lower("bg")

    def place_content(ox, oy):
        """同步设置图片与描边位置（描边严格在图片外围）。"""
        iw, ih = state["img"].width(), state["img"].height()
        canvas.coords(state["img_id"], ox, oy)
        # 边界放在 ox-1 处，width=2 → 线条覆盖 [ox-2, ox]，不压图片
        canvas.coords(
            state["border_id"],
            ox - 1, oy - 1, ox + iw + 1, oy + ih + 1,
        )

    def place_image(reset_center=False):
        """根据当前窗口/图片尺寸，决定居中还是 clamp 拖动偏移。"""
        if state["img_id"] is None:
            return
        w, h = csize()
        iw, ih = state["img"].width(), state["img"].height()
        ox, oy = state["off"]
        pad = BORDER_WIDTH                    # 描边外沿额外占用的像素

        if iw <= w:                           # 图片比窗口窄：强制居中，不可动
            ox = (w - iw) // 2
        else:                                 # 图片比窗口宽：可拖，连描边一起限制
            if reset_center:
                ox = (w - iw) // 2
            ox = min(pad, max(w - iw - pad, ox))

        if ih <= h:
            oy = (h - ih) // 2
        else:
            if reset_center:
                oy = (h - ih) // 2
            oy = min(pad, max(h - ih - pad, oy))

        state["off"] = [ox, oy]
        place_content(ox, oy)

    def rezoom(reset_center=True):
        z = max(1, min(state["zoom"], 32))
        state["zoom"] = z
        # zoom() 是整数倍像素复制（最近邻），放大不模糊
        state["img"] = base.zoom(z) if z > 1 else base
        if state["img_id"] is None:
            state["img_id"] = canvas.create_image(
                0, 0, anchor="nw", image=state["img"]
            )
            state["border_id"] = canvas.create_rectangle(
                0, 0, 0, 0,
                outline=BORDER_COLOR, width=BORDER_WIDTH,
            )
        else:
            canvas.itemconfig(state["img_id"], image=state["img"])
        canvas.tag_raise(state["img_id"])
        canvas.tag_raise(state["border_id"])
        place_image(reset_center=reset_center)

    def zoom_by(delta):
        state["zoom"] += delta
        rezoom(reset_center=True)

    # ---------- 事件 ----------
    def on_configure(_e):
        draw_checkerboard()
        place_image(reset_center=False)

    def on_press(e):
        if state["img_id"] is None:
            return
        iw, ih = state["img"].width(), state["img"].height()
        ox, oy = state["off"]
        if ox <= e.x <= ox + iw and oy <= e.y <= oy + ih:
            state["dragging"] = True
            state["last"] = (e.x, e.y)
            canvas.config(cursor="fleur")
            canvas.grab_set()

    def on_drag(e):
        if not state["dragging"]:
            return
        dx = e.x - state["last"][0]
        dy = e.y - state["last"][1]
        state["last"] = (e.x, e.y)
        state["off"][0] += dx
        state["off"][1] += dy
        place_image(reset_center=False)

    def on_release(_e):
        if state["dragging"]:
            state["dragging"] = False
            canvas.config(cursor="")
            canvas.grab_release()

    canvas.bind("<Configure>", on_configure)
    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    root.bind("<ButtonRelease-1>", on_release)

    canvas.bind("<MouseWheel>", lambda e: zoom_by(1 if e.delta > 0 else -1))
    canvas.bind("<Button-4>", lambda e: zoom_by(1))
    canvas.bind("<Button-5>", lambda e: zoom_by(-1))
    for k in ("<plus>", "<equal>", "<KP_Add>"):
        root.bind(k, lambda e: zoom_by(1))
    for k in ("<minus>", "<KP_Subtract>"):
        root.bind(k, lambda e: zoom_by(-1))
    root.bind("<Escape>", lambda e: root.destroy())

    def init():
        draw_checkerboard()
        rezoom(reset_center=True)

    root.after(0, init)
    root.mainloop()


# ---------- main ----------
def main() -> None:
    parser = argparse.ArgumentParser(description="根据 Minecraft 正版用户名下载或预览皮肤")
    parser.add_argument("username", help="Minecraft 正版用户名")
    parser.add_argument("-o", "--output", help="输出 PNG 路径（默认 <username>.png）")
    parser.add_argument("-v", "--view", action="store_true",
                        help="不保存文件，直接在内存中打开预览窗口")
    args = parser.parse_args()

    username = args.username
    print(f"[*] 用户名: {username}")

    uuid = get_uuid(username)
    print(f"[+] UUID: {uuid}")

    profile = get_profile(uuid)
    b64_value = extract_textures_value(profile)
    #print("[+] textures.value (Base64):")
    #print(b64_value)

    skin_url = decode_skin_url(b64_value)
    print(f"[+] 皮肤链接: {skin_url}")

    skin_png = fetch_skin_png(skin_url)

    if args.view:
        print("[+] 打开预览窗口（滚轮 / +/- 缩放，拖拽平移，Esc 关闭）")
        preview_skin(skin_png)
    else:
        out_path = Path(args.output) if args.output else Path(f"{username}.png")
        out_path.write_bytes(skin_png)
        print(f"[+] 已保存到: {out_path.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[!] 错误: {e}", file=sys.stderr)
        sys.exit(1)