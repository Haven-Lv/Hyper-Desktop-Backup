import argparse
import io
import json
import os
import re
import sys
import time
import unicodedata

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import miui_launcher as L

BACKUP_DIR = os.environ.get("MIUI_BACKUP_DIR", "备份记录")


def ts():
    return time.strftime("%Y%m%d_%H%M%S")


def fmt_ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def norm_rows(payload):
    if isinstance(payload, dict):
        for key in ("rows", "items", "favorites"):
            if isinstance(payload.get(key), list):
                return payload[key], payload
        if list(payload.keys()) == [L.B64] or set(payload) <= set(L.FAVORITES_COLS):
            return [payload], payload
    if isinstance(payload, list):
        return payload, {}
    raise SystemExit("无法识别的备份文件结构")


def row_count(payload):
    rows, meta = norm_rows(payload)
    return len(rows), meta


def backup_subdir(root, stamp):
    return os.path.join(root, "备份_%s" % stamp)


def prebackup_subdir(root, stamp):
    return os.path.join(root, "恢复前备份_%s" % stamp)


def backup_cmd(args):
    snap = L.snapshot(serial=args.serial)
    rows = L.read_favorites(snap["dbpath"])
    scr = L.read_screens(snap["dbpath"])
    stamp = ts()
    sub = backup_subdir(args.dir, stamp)
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, "桌面备份_%s.json" % stamp)
    full = {
        "schema": 1,
        "created": fmt_ts(),
        "device": L.device_meta(snap["serial"]),
        "launcher_pkg": L.LAUNCHER_PKG,
        "dbname": snap["dbname"],
        "grid": snap["grid"],
        "widgets_restorable": False,
        "favorites": L.encode_rows(rows),
        "screens": L.encode_rows(scr),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)
    html_path = os.path.join(sub, "桌面备份_%s.html" % stamp)
    render(rows, snap["grid"], html=html_path, print_text=False, meta=full)
    print("已备份: %s" % path)
    print("  设备: %s  数据库: %s  布局网格: %s  项数: %d" % (
        snap["serial"], snap["dbname"], snap["grid"], len(rows)))
    print("  HTML 预览: %s" % html_path)
    print("  说明: 小组件(itemType=19)会随备份保存，但恢复时无法还原(跳过+提醒)。")
    if args.raw:
        import shutil
        raw_dir = os.path.join(sub, "raw_%s" % stamp)
        os.makedirs(raw_dir, exist_ok=True)
        shutil.copyfile(snap["dbpath"], os.path.join(raw_dir, os.path.basename(snap["dbname"])))
        print("  RAW 数据库原件另存于: %s" % raw_dir)
    return snap


def view_cmd(args):
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            payload = json.load(f)
        file_grid = payload.get("grid") if isinstance(payload, dict) else None
    else:
        snap = L.snapshot(serial=args.serial)
        payload = {
            "grid": snap["grid"],
            "screens": L.read_screens(snap["dbpath"]),
            "favorites": L.encode_rows(L.read_favorites(snap["dbpath"])),
        }
        file_grid = snap["grid"]
    rows, meta = norm_rows(payload)
    rows = L.decode_rows(rows)
    grid = file_grid or meta.get("grid") or "4x6"
    html_path = None
    if args.html:
        if args.file:
            parent = os.path.dirname(args.file)
            base = os.path.basename(args.file)
            stem = os.path.splitext(base)[0] + ".html"
            html_path = os.path.join(parent, stem)
        else:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            html_path = os.path.join(BACKUP_DIR, "当前桌面_%s.html" % ts())
    render(rows, grid, html=html_path, meta=payload if isinstance(payload, dict) else None)


def render(rows, grid="4x6", html=None, print_text=True, meta=None):
    try:
        cols_n, rows_n = [int(x) for x in re.split(r"[xX,]|\*", grid)][:2]
    except Exception:
        cols_n, rows_n = 4, 6
    dock = [r for r in rows if r.get("container") == -101]
    pages = {}
    for r in rows:
        if r.get("container") == -100 and (r.get("screen") or 0) >= 1:
            pages.setdefault(r["screen"], []).append(r)
    folders = [r for r in rows if r.get("itemType") in (2, 21, 22)]

    lines = []
    lines.append("Dock: " + "、".join(r["title"] or "?" for r in sorted(dock, key=lambda r: (r.get("cellY") or 0, r.get("cellX") or 0))))
    for sc in sorted(pages):
        lines.append("")
        lines.append("== 桌面%d页 (网格 %s) ==" % (sc, grid))
        lines.extend(_grid_lines(pages[sc], cols_n, rows_n))
    if folders:
        lines.append("")
        lines.append("== 文件夹 ==")
        for f in sorted(folders, key=lambda r: r.get("_id") or 0):
            contents = sorted(
                [r for r in rows if r.get("container") == f.get("_id")],
                key=lambda r: (r.get("cellY") or 0, r.get("cellX") or 0),
            )
            lines.append("  [%s] %s (screen%s 位%s,%s) 含%d项: %s" % (
                f.get("_id"), f.get("title") or "?",
                f.get("screen"), f.get("cellX"), f.get("cellY"), len(contents),
                "、".join(r.get("title") or "?" for r in contents)))
    text = "\n".join(lines)
    if html:
        html_dir = os.path.dirname(os.path.abspath(html)) or "."
        os.makedirs(html_dir, exist_ok=True)
        with open(html, "w", encoding="utf-8") as f:
            f.write(_html_preview(rows, dock, pages, folders, grid, cols_n, rows_n, meta))
    if print_text:
        print(text)
        if html:
            print("\nHTML 预览已生成: %s" % html)


def _cell_label(r):
    t = r.get("itemType")
    label = (r.get("title") or "").strip()
    if t in (2, 21, 22):
        return "[%s]" % label
    if t == 19:
        return "▦%s" % label[:2]
    if t == 0:
        return label[:2] or "?"
    return "(%s%s)" % (t, label[:1])


def _grid_lines(items, cols_n, rows_n):
    mat = [[None] * cols_n for _ in range(rows_n)]
    for r in items:
        x = r.get("cellX") or 0
        y = r.get("cellY") or 0
        sx = r.get("spanX") or 1
        sy = r.get("spanY") or 1
        for yy in range(sy):
            for xx in range(sx):
                if 0 <= y + yy < rows_n and 0 <= x + xx < cols_n:
                    mat[y + yy][x + xx] = "" if (xx or yy) else _cell_label(r)
    out = []
    for y in range(rows_n):
        cells = []
        for x in range(cols_n):
            v = mat[y][x]
            if not v:
                v = "·"
            cells.append(_pad(v))
        out.append("  " + "|".join(cells))
    return out


def _pad(s, n=12):
    s = s or "·"
    t = ""
    for ch in s:
        if _disp_w(t + ch) > n:
            if t and _disp_w(t + "…") <= n:
                t += "…"
            break
        t += ch
    return t + " " * max(0, n - _disp_w(t))


def _disp_w(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _hue(text):
    h = 214
    for ch in (text or "?"):
        h = (h * 31 + ord(ch)) % 360
    return h


def _tile_colors(text):
    h = _hue(text)
    return "hsl(%d 74%% 60%%)" % h, "hsl(%d 70%% 44%%)" % ((h + 24) % 360)


def _initial(text):
    t = (text or "").strip()
    if not t:
        return "?"
    return t[:2] if t[0].isascii() else t[0]


def _layout_items(items, cols_n, rows_n):
    occupied = [[False] * cols_n for _ in range(rows_n)]
    out = []
    for r in items:
        x = int(r.get("cellX") or 0)
        y = int(r.get("cellY") or 0)
        sx = max(1, int(r.get("spanX") or 1))
        sy = max(1, int(r.get("spanY") or 1))
        if not (0 <= x < cols_n and 0 <= y < rows_n):
            continue
        sx = min(sx, cols_n - x)
        sy = min(sy, rows_n - y)
        if any(occupied[y + yy][x + xx] for yy in range(sy) for xx in range(sx)):
            continue
        for yy in range(sy):
            for xx in range(sx):
                occupied[y + yy][x + xx] = True
        out.append((r, x, y, sx, sy))
    return out, occupied


def _tile_html(r, x, y, sx, sy, counts=None):
    t = r.get("itemType")
    title = (r.get("title") or "").strip()
    if not title:
        title = "小组件" if t == 19 else ("文件夹" if t in (2, 21, 22) else "未命名")
    pos = "grid-column:%d / span %d;grid-row:%d / span %d" % (x + 1, sx, y + 1, sy)
    tip = " title='%s'" % _attr(title)
    if t in (2, 21, 22):
        n = (counts or {}).get(r.get("_id"))
        badge = "<i class='badge'>%d</i>" % n if n else ""
        return (
            "<div class='tile folder clickable' style='%s' data-folder='%s' role='button'"
            " tabindex='0' aria-label='打开文件夹 %s'%s>%s"
            "<div class='icon folder'>%s</div><div class='nm'>%s</div></div>"
            % (pos, _attr(str(r.get("_id"))), _attr(title), tip, badge,
               _esc(_initial(title)), _esc(title))
        )
    if t == 19:
        return (
            "<div class='tile widget' style='%s'%s><div class='wn'>%s</div>"
            "<div class='ws'>%d×%d 小组件</div></div>"
            % (pos, tip, _esc(title), sx, sy)
        )
    c1, c2 = _tile_colors(title + (r.get("intent") or ""))
    return (
        "<div class='tile app' style='%s'%s>"
        "<div class='icon' style='--c1:%s;--c2:%s'>%s</div>"
        "<div class='nm'>%s</div></div>"
        % (pos, tip, c1, c2, _esc(_initial(title)), _esc(title))
    )


def _screen_card(head, note, items, cols_n, rows_n, counts=None, cell=88, gap=10, dock=False):
    laid, occupied = _layout_items(items, cols_n, rows_n)
    parts = []
    for r, x, y, sx, sy in laid:
        parts.append(_tile_html(r, x, y, sx, sy, counts))
    for y in range(rows_n):
        for x in range(cols_n):
            if not occupied[y][x]:
                parts.append(
                    "<div class='empty' style='grid-column:%d;grid-row:%d'></div>" % (x + 1, y + 1))
    return (
        "<div class='%s'><div class='screen-head'>%s<span>%s</span></div>"
        "<div class='grid' style='--cols:%d;--cell:%dpx;--gap:%dpx'>%s</div></div>"
        % ("dock" if dock else "screen", head, note, cols_n, cell, gap, "".join(parts))
    )


def _folder_modals(rows, folders):
    out = []
    for f in sorted(folders, key=lambda r: r.get("_id") or 0):
        children = [r for r in rows if r.get("container") == f.get("_id")]
        if not children:
            continue
        children.sort(key=lambda r: (r.get("cellX") or 0, r.get("cellY") or 0))
        fcols = 4
        frows = max(1, (len(children) + fcols - 1) // fcols)
        parts = []
        for i, r in enumerate(children):
            parts.append(_tile_html(r, i % fcols, i // fcols, 1, 1))
        for i in range(len(children), fcols * frows):
            parts.append(
                "<div class='empty' style='grid-column:%d;grid-row:%d'></div>"
                % (i % fcols + 1, i // fcols + 1))
        name = f.get("title") or "文件夹"
        out.append(
            "<div class='modal' id='folder-%s' aria-hidden='true'>"
            "<div class='modal-card' role='dialog' aria-modal='true' aria-label='%s'>"
            "<button class='modal-x' type='button' aria-label='关闭'>×</button>"
            "<div class='modal-head'><h3>%s</h3><span>%d 项 · 单击空白处关闭</span></div>"
            "<div class='grid mgrid' style='--cols:%d;--cell:88px;--gap:14px'>%s</div>"
            "</div></div>"
            % (_attr(str(f.get("_id"))), _attr(name), _esc(name), len(children),
               fcols, "".join(parts)))
    return "".join(out)


def _html_preview(rows, dock, pages, folders, grid, cols_n, rows_n, meta=None):
    meta = meta if isinstance(meta, dict) else {}
    dev = meta.get("device") if isinstance(meta.get("device"), dict) else {}
    chips = []
    if dev.get("model"):
        chips.append(("设备", dev["model"]))
    chips.append(("网格", grid))
    chips.append(("总条目", str(len(rows))))
    chips.append(("桌面页", str(len(pages))))
    if meta.get("created"):
        chips.append(("备份时间", meta["created"]))
    chips_html = "".join("<span class='chip'>%s <b>%s</b></span>" % (_esc(k), _esc(v)) for k, v in chips)

    counts = {}
    for f in folders:
        counts[f.get("_id")] = len([r for r in rows if r.get("container") == f.get("_id")])

    body = ["<div class='wrap'>"]
    body.append("<h1>桌面布局预览<small>MIUI Desktop Snapshot</small></h1>")
    body.append("<div class='chips'>%s</div>" % chips_html)

    if dock:
        dock_items = sorted(dock, key=lambda r: (r.get("cellY") or 0, r.get("cellX") or 0))
        body.append("<h2>Dock</h2><div class='screens'>")
        body.append(_screen_card("Dock", "%d 个应用" % len(dock_items), dock_items,
                                 max(1, len(dock_items)), 1, counts, cell=80, dock=True))
        body.append("</div>")

    body.append("<h2>桌面页</h2><div class='screens'>")
    for sc in sorted(pages):
        body.append(_screen_card(
            "桌面 %d" % sc,
            "%s · %d 项" % (grid, len(pages[sc])),
            pages[sc], cols_n, rows_n, counts))
    body.append("</div>")

    if folders:
        body.append("<h2>文件夹</h2><div class='folders'>")
        for f in sorted(folders, key=lambda r: r.get("_id") or 0):
            contents = sorted(
                [r for r in rows if r.get("container") == f.get("_id")],
                key=lambda r: (r.get("cellY") or 0, r.get("cellX") or 0))
            chips_apps = "".join("<span>%s</span>" % _esc(r.get("title") or "?") for r in contents)
            body.append(
                "<div class='fcard clickable' data-folder='%s' role='button' tabindex='0'"
                " aria-label='打开文件夹 %s'><h3>%s</h3>"
                "<div class='where'>第%s屏 · 第%s列第%s行 · %d 项</div>"
                "<div class='apps'>%s</div></div>" % (
                    _attr(str(f.get("_id"))), _attr(f.get("title") or "?"),
                    _esc(f.get("title") or "?"), f.get("screen") or 0,
                    (f.get("cellX") or 0) + 1, (f.get("cellY") or 0) + 1,
                    len(contents), chips_apps))
        body.append("</div>")

    body.append("<p class='foot'>预览为近似示意图；小组件以占位块显示，恢复时不会还原。</p></div>")
    body.append(_folder_modals(rows, folders))

    html = """<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<style>
*{box-sizing:border-box}
body{margin:0;padding:36px 24px 56px;background:
radial-gradient(1100px 480px at 8% -12%,#e2eaff 0%,transparent 62%),
radial-gradient(900px 460px at 102% -6%,#ffe8d9 0%,transparent 58%),#eef1f6;
color:#1b1f26;font-family:"Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1240px;margin:0 auto}
h1{font-size:22px;margin:0 0 12px;letter-spacing:.5px}
h1 small{font-size:11px;color:#8b94a1;font-weight:400;margin-left:10px;letter-spacing:1px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 28px}
.chip{background:#fff;border:1px solid #e3e7ee;border-radius:999px;padding:5px 12px;font-size:12px;
color:#5b6675;box-shadow:0 1px 2px rgba(20,30,50,.04)}
.chip b{color:#1b1f26;font-weight:600;margin-left:4px}
h2{font-size:12px;color:#7b8593;letter-spacing:1.5px;text-transform:uppercase;margin:0 0 12px;}
.screens{display:flex;flex-wrap:wrap;gap:26px;align-items:flex-start;margin-bottom:30px}
.screen,.dock{background:#fff;border-radius:22px;padding:16px;border:1px solid #edf0f5;
box-shadow:0 16px 34px -22px rgba(20,35,70,.45),0 2px 6px rgba(20,35,70,.05)}
.dock{display:inline-block}
.screen-head{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
margin:0 2px 12px;font-size:13px;color:#39424f;font-weight:600}
.screen-head span{font-size:11px;color:#98a1ad;font-weight:400}
.grid{display:grid;gap:var(--gap);grid-auto-rows:var(--cell);
grid-template-columns:repeat(var(--cols),var(--cell))}
.tile{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;
border-radius:16px;padding:6px 3px;overflow:hidden;position:relative}
.icon{width:44px;height:44px;border-radius:14px;display:flex;align-items:center;
justify-content:center;color:#fff;font-weight:700;font-size:17px;flex:none;
background:linear-gradient(150deg,var(--c1),var(--c2));
box-shadow:0 6px 14px -6px rgba(15,25,45,.5),inset 0 1px 0 rgba(255,255,255,.35)}
.nm{font-size:10.5px;line-height:1.2;color:#3a4453;max-width:100%;text-align:center;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
word-break:break-word;transition:color .15s}
.tile.clickable{cursor:pointer;transition:transform .12s ease,box-shadow .12s ease}
.tile.clickable:hover{transform:translateY(-2px)}
.tile.clickable:hover .nm{color:#1b1f26}
.tile.clickable:focus-visible,.fcard.clickable:focus-visible{outline:2px solid #6f9bff;outline-offset:2px}
.empty{border:1px dashed #e9ecf2;border-radius:16px;background:rgba(248,250,253,.6)}
.tile.folder{background:linear-gradient(160deg,rgba(255,244,226,.95),rgba(255,229,197,.55));
border:1px solid #ffe2ba}
.icon.folder{background:linear-gradient(150deg,#ffd28d,#ffab40);font-size:15px}
.badge{position:absolute;top:5px;right:7px;background:#fff;color:#b26a00;border:1px solid #ffd9a0;
border-radius:999px;padding:0 6px;font-size:10px;line-height:16px;font-weight:700;font-style:normal}
.tile.widget{align-items:flex-start;justify-content:flex-end;padding:12px;
background:linear-gradient(140deg,#eaf1ff,#dbe7ff);border:1px solid #cddffd}
.tile.widget .wn{font-size:13px;font-weight:600;color:#2b4a86;text-align:left}
.tile.widget .ws{font-size:10px;color:#718cc4}
.folders{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:26px}
.fcard{background:#fff;border:1px solid #edf0f5;border-radius:18px;padding:14px 16px;
min-width:230px;max-width:360px;box-shadow:0 12px 26px -22px rgba(20,35,70,.5)}
.fcard.clickable{cursor:pointer;transition:transform .12s ease,box-shadow .12s ease,border-color .12s}
.fcard.clickable:hover{transform:translateY(-2px);border-color:#d9e3f5;
box-shadow:0 18px 30px -22px rgba(20,35,70,.55)}
.fcard h3{margin:0 0 4px;font-size:14px}
.fcard .where{font-size:11px;color:#98a1ad;margin-bottom:10px}
.apps{display:flex;flex-wrap:wrap;gap:6px}
.apps span{background:#f3f6fb;border:1px solid #e6ecf6;border-radius:999px;
padding:3px 9px;font-size:11px;color:#4a5666}
.foot{color:#98a1ad;font-size:12px;margin:6px 0 0}
.modal{position:fixed;inset:0;display:none;align-items:center;justify-content:center;padding:28px;
background:rgba(12,18,32,.42);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);z-index:60}
.modal.open{display:flex}
.modal-card{position:relative;background:rgba(255,255,255,.94);border:1px solid rgba(255,255,255,.7);
border-radius:28px;padding:22px 24px 26px;max-width:min(94vw,470px);max-height:88vh;overflow:auto;
box-shadow:0 40px 90px -34px rgba(10,20,40,.6),0 2px 8px rgba(10,20,40,.08);
animation:pop .18s ease;scrollbar-width:thin;overscroll-behavior:contain}
@keyframes pop{from{opacity:0;transform:scale(.94)}to{opacity:1;transform:none}}
.modal-head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;
margin:0 2px 18px;padding-right:38px}
.modal-head h3{margin:0;font-size:17px;letter-spacing:.3px}
.modal-head span{font-size:11px;color:#98a1ad;white-space:nowrap}
.modal-x{position:absolute;top:14px;right:14px;width:30px;height:30px;border-radius:50%;border:none;
background:#eef1f6;color:#5b6675;font-size:18px;line-height:1;cursor:pointer;
display:flex;align-items:center;justify-content:center;padding:0}
.modal-x:hover{background:#e2e7f0;color:#1b1f26}
</style></head><body>
__BODY__
<script>
(function(){
  var lastFocus=null;
  function openFolder(id){
    var m=document.getElementById('folder-'+id);
    if(!m) return;
    lastFocus=document.activeElement;
    m.classList.add('open');
    m.setAttribute('aria-hidden','false');
    var x=m.querySelector('.modal-x');
    if(x) x.focus();
  }
  function closeModal(m){
    if(!m) return;
    m.classList.remove('open');
    m.setAttribute('aria-hidden','true');
    if(lastFocus && lastFocus.focus){ try{ lastFocus.focus(); }catch(e){} }
  }
  var triggers=document.querySelectorAll('[data-folder]');
  for(var i=0;i<triggers.length;i++){
    (function(el){
      el.addEventListener('click', function(){ openFolder(el.getAttribute('data-folder')); });
      el.addEventListener('keydown', function(e){
        if(e.key==='Enter' || e.key===' '){ e.preventDefault(); openFolder(el.getAttribute('data-folder')); }
      });
    })(triggers[i]);
  }
  var modals=document.querySelectorAll('.modal');
  for(var j=0;j<modals.length;j++){
    (function(m){
      m.addEventListener('click', function(e){ if(e.target===m) closeModal(m); });
      var x=m.querySelector('.modal-x');
      if(x) x.addEventListener('click', function(){ closeModal(m); });
    })(modals[j]);
  }
  document.addEventListener('keydown', function(e){
    if(e.key!=='Escape') return;
    var opened=document.querySelectorAll('.modal.open');
    if(opened.length) closeModal(opened[opened.length-1]);
  });
})();
</script>
</body></html>"""
    return html.replace("__BODY__", "\n".join(body))


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attr(s):
    return _esc(s).replace('"', "&quot;").replace("'", "&#39;")


def resolve_pkg(r):
    intent = r.get("intent") or ""
    m = re.search(r"component=([^;/]+)", intent)
    if m:
        return m.group(1)
    return r.get("iconPackage")


def choose_backup_file(args):
    if args.file:
        return args.file
    import glob
    files = sorted(glob.glob(os.path.join(args.dir, "备份_*", "*.json")),
                   key=lambda p: os.path.getmtime(p), reverse=True)
    if not files:
        raise SystemExit("没有找到可恢复的备份 JSON（目录: %s）" % args.dir)
    print("在 %s 中发现 %d 份备份 JSON：" % (args.dir, len(files)))
    for i, fp in enumerate(files, 1):
        created = ""
        count = ""
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
            created = d.get("created", "")
            nb = d.get("favorites")
            count = "共%d项" % (len(nb) if isinstance(nb, list) else 0)
        except Exception:
            pass
        print("  [%d] %s  %s  %s" % (i, os.path.basename(fp), created, count))
    try:
        ans = input("输入编号选择要恢复的备份(直接回车=q 取消): ").strip()
    except EOFError:
        ans = ""
    if not ans or ans.lower() == "q":
        raise SystemExit("已取消")
    try:
        k = int(ans) - 1
    except ValueError:
        raise SystemExit("编号无效")
    if not (0 <= k < len(files)):
        raise SystemExit("编号无效")
    return files[k]


def restore_cmd(args):
    chosen = choose_backup_file(args)
    print("使用备份: %s\n" % chosen)
    with open(chosen, encoding="utf-8") as f:
        payload = json.load(f)
    rows, meta = norm_rows(payload)
    rows = L.decode_rows(rows)
    grid = meta.get("grid") or payload.get("grid") or "4x6"

    snap = L.snapshot(serial=args.serial)
    serial = snap["serial"]
    installed = L.installed_packages(serial)
    print("设备: %s  已连接" % serial)
    print("已获取已安装应用 %d 个" % len(installed))

    skipped = []
    kept = []
    for r in rows:
        t = r.get("itemType")
        if not args.keep_widgets and t == 19:
            skipped.append((r, "小组件在恢复中无法还原，已跳过"))
            continue
        pkg = resolve_pkg(r)
        if t == 0 and pkg and pkg not in installed:
            pkg2 = (pkg.split("/")[0])
            if pkg2 not in installed:
                skipped.append((r, "应用未安装: %s" % pkg2))
                continue
        kept.append(r)

    seen = set()
    dedup = []
    for r in kept:
        if r.get("itemType") == 0:
            pkg = resolve_pkg(r)
            if pkg:
                if pkg in seen and (r.get("container") in (-100, -101) or True):
                    skipped.append((r, "与应用重复，已跳过重复项"))
                    continue
                seen.add(pkg)
        dedup.append(r)

    if skipped:
        print("\n以下 %d 项将跳过（不写库，不影响其他项）：" % len(skipped))
        for r, why in skipped:
            print("  - %s  (%s)" % ((r.get("title") or "?"), why))
    if not dedup:
        raise SystemExit("没有可写入的项，已中止")
    print("\n将写入 %d 项（原备份 %d 项，跳过 %d 项）" % (len(dedup), len(rows), len(skipped)))

    presub = prebackup_subdir(args.dir, ts())
    os.makedirs(presub, exist_ok=True)
    pre = os.path.join(presub, "恢复前备份_%s.json" % ts())
    pre_payload = {
        "schema": 1,
        "created": fmt_ts(),
        "device": L.device_meta(serial),
        "grid": grid,
        "note": "restore 前自动备份",
        "favorites": L.encode_rows(L.read_favorites(snap["dbpath"])),
        "screens": L.encode_rows(L.read_screens(snap["dbpath"])),
    }
    with open(pre, "w", encoding="utf-8") as f:
        json.dump(pre_payload, f, ensure_ascii=False, indent=2)
    print("已自动备份恢复前状态: %s" % pre)

    if not args.yes:
        try:
            ans = input("确认恢复? 输入 yes: ")
        except EOFError:
            ans = ""
        if ans.strip().lower() != "yes":
            raise SystemExit("已取消")

    dbpath = _build_db(snap["dbpath"], dedup)
    rc, out, err = L.apply_new_db(snap["dbname"], dbpath, serial=serial, owner=snap["owner"])
    if "APPLY_DONE" not in out:
        raise SystemExit("写库失败：%s" % out[-500:])
    L.restart_launcher(serial)
    time.sleep(2)
    vsnap = L.snapshot(serial=serial)
    vrows = L.read_favorites(vsnap["dbpath"])
    print("写库成功，桌面已重启。回读校验: %d 项 (期望 %d 项) %s" % (
        len(vrows), len(dedup),
        "OK" if len(vrows) == len(dedup) else "[注意:数量不一致，可在恢复前备份中找回]"))


def _build_db(src_dbpath, rows):
    import shutil
    import tempfile
    workdir = tempfile.mkdtemp(prefix="miui_restore_")
    dbpath = os.path.join(workdir, "launcher.db")
    shutil.copyfile(src_dbpath, dbpath)
    con = sqlite3_connect(dbpath)
    cur = con.cursor()
    existing = {r[1] for r in cur.execute("PRAGMA table_info(favorites)").fetchall()}
    cols = [c for c in L.FAVORITES_COLS if c in existing]
    cur.execute("DELETE FROM favorites")
    for r in rows:
        row = {}
        for c in cols:
            row[c] = r.get(c) if r.get(c) is not None else None
        if "appWidgetId" in cols:
            row["appWidgetId"] = row.get("appWidgetId") if row.get("appWidgetId") is not None else -1
        if "itemFlags" in cols:
            row["itemFlags"] = row.get("itemFlags") if row.get("itemFlags") is not None else 0
        if "profileId" in cols:
            row["profileId"] = row.get("profileId") if row.get("profileId") is not None else 0
        if "originWidgetId" in cols:
            row["originWidgetId"] = row.get("originWidgetId") if row.get("originWidgetId") is not None else -1
        if "widgetConfigSpanX" in cols:
            row["widgetConfigSpanX"] = row.get("widgetConfigSpanX") if row.get("widgetConfigSpanX") is not None else -1
        if "widgetConfigSpanY" in cols:
            row["widgetConfigSpanY"] = row.get("widgetConfigSpanY") if row.get("widgetConfigSpanY") is not None else -1
        q = ",".join("?" * len(cols))
        cur.execute("INSERT INTO favorites (%s) VALUES (%s)" % (",".join(cols), q),
                    [row[c] for c in cols])
    pages = sorted({r["screen"] for r in rows if r.get("container") == -100 and (r.get("screen") or 0) >= 1})
    cur.execute("DELETE FROM screens")
    for i, p in enumerate(pages):
        cur.execute("INSERT INTO screens (_id, title, screenOrder, screenType) VALUES (?,?,?,0)",
                    (p, None, i))
    con.commit()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    return dbpath


def sqlite3_connect(dbpath):
    import sqlite3
    return sqlite3.connect(dbpath)


def list_cmd(args):
    ds = L.devices()
    if not ds:
        print("没有检测到已连接的设备")
        return
    for d in ds:
        rc, out, err = L.shell("getprop ro.product.model")
        print("%s   %s" % (d, out.strip()))


def main():
    p = argparse.ArgumentParser(prog="miui_desktop", description="小米 MIUI 桌面备份/查看/恢复工具")
    p.add_argument("--adb", default=None, help="adb 可执行文件路径")
    p.add_argument("--serial", default=None, help="设备序列号(默认自动选择)")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup", help="备份当前桌面(每次在 备份记录 下创建 备份_时间戳 文件夹, 并默认生成HTML预览)")
    b.add_argument("--dir", default=BACKUP_DIR, help="备份根目录(默认: 当前目录下的 备份记录)")
    b.add_argument("--raw", action="store_true", help="同时保留原始数据库文件一份(放当前备份文件夹下)")
    b.set_defaults(func=backup_cmd)

    v = sub.add_parser("view", help="查看布局(默认读手机当前状态, 或指定备份JSON)")
    v.add_argument("file", nargs="?", help="备份/布局 JSON 文件")
    v.add_argument("--html", action="store_true", help="同时生成 HTML 预览")
    v.set_defaults(func=view_cmd)

    r = sub.add_parser("restore", help="从备份JSON恢复桌面(不带文件则自动扫描 备份记录 下各备份子文件夹让您选择)")
    r.add_argument("file", nargs="?", help="备份 JSON 文件(省略则自动扫描选择)")
    r.add_argument("--dir", default=BACKUP_DIR, help="恢复前自动备份目录(默认: 当前目录下的 备份记录)")
    r.add_argument("--keep-widgets", action="store_true", help="尝试保留小组件(不建议，通常无法还原)")
    r.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    r.set_defaults(func=restore_cmd)

    ls = sub.add_parser("list", help="列出已连接设备")
    ls.set_defaults(func=list_cmd)

    args = p.parse_args()
    if args.adb:
        os.environ["MIUI_ADB"] = args.adb
    args.func(args)


if __name__ == "__main__":
    main()