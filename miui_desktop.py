import argparse
import io
import json
import os
import re
import sys
import time

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
    render(rows, snap["grid"], html=html_path, print_text=False)
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
    render(rows, grid, html=html_path)


def render(rows, grid="4x6", html=None, print_text=True):
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
            f.write(_html_preview(rows, dock, pages, folders, grid, cols_n, rows_n))
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
            if v is None:
                v = "·"
            elif v == "":
                v = "·"
            cells.append(v)
        uniq = _de_dupe_row(cells)
        out.append("  " + " | ".join(u.ljust(6) for u in uniq))
    return out


def _de_dupe_row(cells):
    res = []
    prev = None
    for c in cells:
        if c == prev and c == "·":
            continue
        res.append(c)
        prev = c
    return res


def _html_preview(rows, dock, pages, folders, grid, cols_n, rows_n):
    css = """
body{font-family:"Microsoft YaHei",sans-serif;background:#f5f6f8;color:#222;padding:20px}
h2{border-left:4px solid #ff7800;padding-left:8px}
.page{background:#fff;border-radius:10px;padding:12px 16px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.12)}
table{border-collapse:collapse;width:100%;table-layout:fixed}
td{border:1px solid #e2e4e8;text-align:center;vertical-align:middle;height:44px;font-size:15px}
td.app{background:#fff}
td.folder{background:#fff3e0;border-color:#ffd591;color:#ad6800;font-weight:bold}
td.widget{background:#e8f0fe;color:#1a56c4}
td.empty{background:#fafbfc;color:#cfd4da}
.dock td{height:52px}
.folders li{line-height:1.7}
"""
    b = ["<!doctype html><html><head><meta charset='utf-8'><style>%s</style></head><body>" % css]
    b.append("<h1>桌面布局预览</h1>")
    b.append("<h2>Dock</h2><div class='page dock'><table><tr>" +
             "".join("<td class='app'>%s</td>" % _esc(r.get("title")) for r in sorted(dock, key=lambda r: (r.get("cellY") or 0, r.get("cellX") or 0))) +
             "</tr></table></div>")
    for sc in sorted(pages):
        b.append("<h2>桌面%d页 (%s)</h2><div class='page'><table>" % (sc, grid))
        mat = [[None] * cols_n for _ in range(rows_n)]
        for r in pages[sc]:
            x = r.get("cellX") or 0
            y = r.get("cellY") or 0
            sx = r.get("spanX") or 1
            sy = r.get("spanY") or 1
            cls = {2: "folder", 21: "folder", 22: "folder", 19: "widget", 0: "app"}.get(r.get("itemType"), "app")
            mat[y][x] = ("<td rowspan='%d' colspan='%d' class='%s'>%s</td>" % (
                sy, sx, cls, _esc(r.get("title") or "")))
        for y in range(rows_n):
            cells = []
            for x in range(cols_n):
                if mat[y][x]:
                    cells.append(mat[y][x])
                else:
                    cells.append("<td class='empty'>&nbsp;</td>")
            b.append("<tr>" + "".join(cells) + "</tr>")
        b.append("</table></div>")
    if folders:
        b.append("<h2>文件夹</h2><div class='page'><ul class='folders'>")
        for f in sorted(folders, key=lambda r: r.get("_id") or 0):
            contents = sorted(
                [r for r in rows if r.get("container") == f.get("_id")],
                key=lambda r: (r.get("cellY") or 0, r.get("cellX") or 0))
            b.append("<li><b>%s</b> (第%d屏): %s</li>" % (
                _esc(f.get("title") or "?"), f.get("screen") or 0,
                "、".join(_esc(r.get("title") or "?") for r in contents)))
        b.append("</ul></div>")
    b.append("<p style='color:#888'>预览为近似示意图；小组件与特殊图标以占位呈现。</p></body></html>")
    return "\n".join(b)


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    cur.execute("DELETE FROM favorites")
    for r in rows:
        row = {}
        for c in L.FAVORITES_COLS:
            row[c] = r.get(c) if r.get(c) is not None else None
        row["appWidgetId"] = row.get("appWidgetId") if row.get("appWidgetId") is not None else -1
        row["itemFlags"] = row.get("itemFlags") if row.get("itemFlags") is not None else 0
        row["profileId"] = row.get("profileId") if row.get("profileId") is not None else 0
        row["originWidgetId"] = row.get("originWidgetId") if row.get("originWidgetId") is not None else -1
        row["widgetConfigSpanX"] = row.get("widgetConfigSpanX") if row.get("widgetConfigSpanX") is not None else -1
        row["widgetConfigSpanY"] = row.get("widgetConfigSpanY") if row.get("widgetConfigSpanY") is not None else -1
        q = ",".join("?" * len(L.FAVORITES_COLS))
        cur.execute("INSERT INTO favorites (%s) VALUES (%s)" % (",".join(L.FAVORITES_COLS), q),
                    [row[c] for c in L.FAVORITES_COLS])
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