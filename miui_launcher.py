import os
import re
import subprocess
import time
import tempfile
import sqlite3
import base64
import shutil

ADB = os.environ.get("MIUI_ADB", r"D:\Software\adb\platform-tools\adb.exe")
LAUNCHER_PKG = os.environ.get("MIUI_LAUNCHER", "com.miui.home")
DATABASES_DIR = os.environ.get(
    "MIUI_DB_DIR", "/data/user_de/0/com.miui.home/databases"
)

FAVORITES_COLS = [
    "_id", "title", "intent", "container", "extendContainer", "isOnlySupportExtend",
    "screen", "cellX", "cellY", "spanX", "spanY", "itemType", "appWidgetId",
    "isShortcut", "iconType", "iconPackage", "iconResource", "icon", "uri",
    "displayMode", "launchCount", "sortMode", "itemFlags", "profileId", "label",
    "appWidgetProvider", "originWidgetId", "product_id", "widgetConfigSpanX",
    "widgetConfigSpanY",
]
SCREENS_COLS = ["_id", "title", "screenOrder", "screenType"]

B64 = "$b64"


def _sh_single_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def run_adb(args, timeout=90):
    try:
        r = subprocess.run(
            [ADB] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        raise SystemExit("未找到 adb: %s（可用环境变量 MIUI_ADB 指定）" % ADB)


def shell(cmd, timeout=90):
    return run_adb(["shell", cmd], timeout)


def pull(remote, local):
    return run_adb(["pull", remote, local])


def push(local, remote, timeout=120):
    return run_adb(["push", local, remote], timeout)


def devices():
    rc, out, err = run_adb(["devices"])
    res = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            res.append(parts[0])
    return res


def pick_serial():
    ds = devices()
    if not ds:
        raise SystemExit("没有检测到已连接的设备（adb devices 为空）")
    if len(ds) == 1:
        return ds[0]
    print("检测到多台设备：")
    for i, d in enumerate(ds, 1):
        print("  [%d] %s" % (i, d))
    try:
        k = int(input("请选择设备编号: ")) - 1
    except (ValueError, EOFError):
        raise SystemExit("取消")
    if not (0 <= k < len(ds)):
        raise SystemExit("编号无效")
    return ds[k]


REMOTE_SCRATCH = "/data/local/tmp"
REMOTE_SD = "/sdcard/ltmp/mdtool"


def _exe_device_script(name, script, serial):
    local = os.path.join(tempfile.gettempdir(), name)
    with open(local, "w", encoding="ascii", newline="\n") as f:
        f.write(script)
    remote = REMOTE_SCRATCH + "/" + name
    shell("mkdir -p " + REMOTE_SD, timeout=30)
    push(local, remote)
    rc, out, err = shell(
        "su -c " + _sh_single_quote("sh %s" % remote), timeout=120
    )
    shell("rm -f " + remote, timeout=30)
    try:
        os.remove(local)
    except OSError:
        pass
    return rc, out, err


def snapshot(serial=None, workdir=None):
    serial = serial or pick_serial()
    workdir = workdir or tempfile.mkdtemp(prefix="miui_snap_")
    stamp = str(int(time.time()))
    script = """#!/system/bin/sh
D=%s
S=%s/%s
mkdir -p $S
DB=$(ls -1 $D/launcher*.db 2>/dev/null | head -n1)
[ -z "$DB" ] && { echo "NO_DB"; exit 1; }
cp "$DB" "$S/db"
[ -f "${DB}-wal" ] && cp "${DB}-wal" "$S/db-wal" || true
[ -f "${DB}-shm" ] && cp "${DB}-shm" "$S/db-shm" || true
stat -c "OWNER=%%u:%%g" "$DB"
echo "DB=$DB"
echo "SNAP_DONE"
""" % (DATABASES_DIR, REMOTE_SD, stamp)
    rc, out, err = _exe_device_script("snap_%s.sh" % stamp, script, serial)
    if "NO_DB" in out:
        raise SystemExit("设备上未找到桌面数据库: %s/launcher*.db" % DATABASES_DIR)
    dbname = None
    owner = None
    for line in out.splitlines():
        if line.startswith("DB="):
            dbname = line[3:].strip()
        if line.startswith("OWNER="):
            owner = line[6:].strip()
    if not dbname:
        raise SystemExit("快照脚本未报告数据库（输出：%r）" % out)
    base = os.path.basename(dbname)
    pull(REMOTE_SD + "/" + stamp + "/db", os.path.join(workdir, "launcher.db"))
    pull(REMOTE_SD + "/" + stamp + "/db-wal", os.path.join(workdir, "launcher.db-wal"))
    pull(REMOTE_SD + "/" + stamp + "/db-shm", os.path.join(workdir, "launcher.db-shm"))
    shell("rm -rf " + REMOTE_SD + "/" + stamp, timeout=30)
    dbpath = os.path.join(workdir, "launcher.db")
    con = sqlite3.connect(dbpath)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    return {
        "serial": serial,
        "dbname": dbname,
        "dbpath": dbpath,
        "workdir": workdir,
        "owner": owner,
        "grid": guess_grid(dbname),
    }


def guess_grid(dbname):
    m = re.search(r"launcher(\d+)x(\d+)\.db", dbname or "")
    if m:
        return "%sx%s" % (m.group(1), m.group(2))
    return "4x6"


def read_favorites(dbpath):
    con = sqlite3.connect(dbpath)
    cur = con.cursor()
    rows = []
    for r in cur.execute("SELECT %s FROM favorites" % ",".join(FAVORITES_COLS)):
        rows.append(dict(zip(FAVORITES_COLS, r)))
    con.close()
    return rows


def read_screens(dbpath):
    con = sqlite3.connect(dbpath)
    cur = con.cursor()
    rows = []
    for r in cur.execute("SELECT %s FROM screens" % ",".join(SCREENS_COLS)):
        rows.append(dict(zip(SCREENS_COLS, r)))
    con.close()
    return rows


def encode_rows(rows):
    out = []
    for r in rows:
        row = dict(r)
        for k, v in row.items():
            if isinstance(v, bytes):
                row[k] = {B64: base64.b64encode(v).decode("ascii")}
        out.append(row)
    return out


def decode_rows(rows):
    out = []
    for r in rows:
        row = dict(r)
        for k, v in row.items():
            if isinstance(v, dict) and v.get(B64):
                row[k] = base64.b64decode(v[B64])
        out.append(row)
    return out


def device_meta(serial):
    meta = {"serial": serial}
    for key, prop in (
        ("model", "ro.product.model"),
        ("miui_version", "ro.mi.os.version.name"),
        ("build", "ro.build.display.id"),
    ):
        rc, out, err = shell("getprop %s" % prop)
        meta[key] = out.strip()
    return meta


def installed_packages(serial):
    rc, out, err = shell("pm list packages")
    pkgs = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.add(line[len("package:"):])
    return pkgs


def apply_new_db(dbname, dbpath, serial=None, owner=None):
    serial = serial or pick_serial()
    owner = owner or "u0_a138:u0_a138"
    stamp = str(int(time.time()))
    base = os.path.basename(dbname)
    local = os.path.join(tempfile.gettempdir(), "newdb_%s.db" % stamp)
    shutil.copyfile(dbpath, local)
    push(local, REMOTE_SCRATCH + "/newdb_%s.db" % stamp)
    os.remove(local)
    script = """#!/system/bin/sh
D=%s
DB=$D/%s
mkdir -p /sdcard/backup/mdtool
mkdir -p %s
cp $DB /sdcard/backup/mdtool/pre_%s_%s 2>/dev/null || true
cp %s/newdb_%s.db $DB
rm -f ${DB}-wal ${DB}-shm
chown %s $DB
chmod 600 $DB
rm -f %s/newdb_%s.db
echo APPLY_DONE
""" % (DATABASES_DIR, base, REMOTE_SD, stamp, base,
       REMOTE_SCRATCH, stamp, owner, REMOTE_SCRATCH, stamp)
    rc, out, err = _exe_device_script("apply_%s.sh" % stamp, script, serial)
    return rc, out, err


def restart_launcher(serial=None):
    serial = serial or pick_serial()
    shell("am force-stop %s" % LAUNCHER_PKG)
    time.sleep(1.0)
    shell("am start -n %s/.launcher.Launcher" % LAUNCHER_PKG)