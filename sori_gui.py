# -*- coding: utf-8 -*-
"""
SORI 데스크톱 실행 화면 (tkinter) — 더블클릭 앱(SORI.app / SORI.exe)의 진입점.
계산 엔진(sori.build / sori.run / 그림)은 고치지 않고 호출만 함. 명령행 `python -m sori all` 과 같은 순서.

  python sori_gui.py                                   화면 실행(지난번에 쓴 설정 파일을 미리 선택)
  python sori_gui.py --config config.yaml              이 설정 파일을 미리 선택해 화면 실행
  python sori_gui.py --run config.yaml --out 결과폴더   화면 없이 같은 작업 실행(점검용)
  python sori_gui.py --selfcheck report.json           자가 점검: 좌표 변환 + 기준 사례 계산·비교 → JSON

※ 본 소프트웨어는 Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import datetime as dt
import json
import logging
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sori.resources import (default_output_dir, is_frozen, resource_path,  # noqa: E402
                            setup_numba_cache, user_data_dir)

setup_numba_cache()                      # sori 계산 모듈(numba) import 전에 캐시 폴더 지정

from sori import AI_NOTICE, __version__  # noqa: E402

log = logging.getLogger("sori")
PERIODS = {"주간 LrT (06–22시)": "LrT", "야간 LrN (22–06시)": "LrN"}


# ================================================================ 화면 설정 기억(마지막 설정 파일·결과 폴더)
def settings_path():
    return user_data_dir() / "settings.json"


def load_settings():
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(**kv):
    s = load_settings()
    s.update({k: str(v) for k, v in kv.items()})
    try:
        settings_path().write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ================================================================ 계산 작업(화면과 무관)
def run_job(cfg_path, out_dir, which=("grid", "facade", "points"), do_plots=True, period="LrT", progress=None):
    """설정 파일로 build → run → (그림) 실행. 결과·사용 설정·계산 조건은 out_dir 에 저장"""
    from sori import config as C, build, run
    cfg_path = Path(cfg_path).resolve()
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cfg = C.load(cfg_path)
    cfg["project"]["output_dir"] = str(out)
    shutil.copy(cfg_path, out / "config_used.yaml")
    cond = {
        "software": f"SORI v{__version__}",
        "notice": AI_NOTICE,
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "config_path": str(cfg_path),
        "config_copy": "config_used.yaml (상대경로는 config_path 폴더 기준)",
        "output_dir": str(out),
        "targets": list(which),
        "plots": bool(do_plots),
        "summary_period": period,
        "app": {"frozen": is_frozen(), "python": platform.python_version(),
                "os": platform.platform(), "machine": platform.machine()},
        "status": "running",
    }
    cpath = out / "run_conditions.json"
    cpath.write_text(json.dumps(cond, ensure_ascii=False, indent=2), encoding="utf-8")
    t0 = time.time()
    try:
        log.info("① 계산 모델 만들기 (build)")
        build.main(cfg)
        log.info("② 소음 계산 (run) — 처음 한 번은 컴파일에 1분 정도 걸릴 수 있음")
        run.main(cfg, tuple(which), progress=progress)
        files = []
        if do_plots:
            from sori import plots, maps_hq, viewer3d
            log.info("③ 그림 그리기")
            files += plots.main(cfg)
            files += maps_hq.main(cfg)
            if (out / "facade_result.csv").exists() and (out / "grid_result.csv").exists():
                files.append(viewer3d.main(cfg))
        summary = summarize(out, period)
        for line in summary:
            log.info("요약 %s", line)
        cond.update(status="done", elapsed_s=round(time.time() - t0, 1), summary=summary,
                    files=[os.path.relpath(f, out) for f in files])
        return cond
    except Exception:
        cond.update(status="error", elapsed_s=round(time.time() - t0, 1), error=traceback.format_exc())
        raise
    finally:
        cpath.write_text(json.dumps(cond, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize(out, key):
    """선택한 시간대 지표(LrT/LrN)의 결과 요약 문장 — 계산은 주·야 모두 수행됨"""
    import pandas as pd
    lines = []
    g = out / "grid_result.csv"
    if g.exists():
        G = pd.read_csv(g)
        for h, s in G.groupby("h")[key]:
            lines.append(f"격자 h={h:g} m {key}: 최소 {s.min():.1f}, 평균 {s.mean():.1f}, 최대 {s.max():.1f} dB ({len(s):,}점)")
    f = out / "facade_result.csv"
    if f.exists():
        F = pd.read_csv(f)
        if len(F):
            lines.append(f"건물 벽면 {key}: 최대 {F[key].max():.1f} dB ({len(F):,}점)")
    p = out / "point_result.csv"
    if p.exists():
        P = pd.read_csv(p)
        if len(P):
            lines.append(f"점 수음점 {key}: {P[key].min():.1f} ~ {P[key].max():.1f} dB ({len(P)}점)")
    return lines


def selfcheck(report_path):
    """좌표 변환과 기준 사례(가상 교차로) 계산을 기준값과 비교. 0 = 통과, 1 = 실패"""
    sys.path.insert(0, str(resource_path("tests")))
    import reference_case as RC
    rep = {"notice": AI_NOTICE, "software": f"SORI v{__version__}", "frozen": is_frozen(),
           "python": platform.python_version(), "os": platform.platform(), "machine": platform.machine(),
           "started": dt.datetime.now().isoformat(timespec="seconds")}
    t0 = time.time()
    try:
        import pyproj
        rep["proj_data_dir"] = pyproj.datadir.get_data_dir()
        rep["proj_version"] = pyproj.proj_version_str
        rep["crs"] = [dict(name=n, max_diff=d, tol=t, ok=bool(ok)) for n, d, t, ok in RC.crs_checks()]
        with tempfile.TemporaryDirectory() as tmp:
            out = RC.run_case(tmp)
            rows = RC.compare(out)
        rep["reference"] = [dict(file=f, col=c, unit=u, max_diff=d, tol=t, ok=bool(ok)) for f, c, u, d, t, ok in rows]
        rep["max_diff_dB"] = max(r["max_diff"] for r in rep["reference"] if r["unit"] == "dB")
        rep["max_diff_m"] = max(r["max_diff"] for r in rep["reference"] if r["unit"] == "m")
        rep["tolerance_note"] = f"허용 오차 {RC.TOL_DB} dB, {RC.TOL_M} m — 프로젝트 임의 설정"
        rep["ok"] = all(r["ok"] for r in rep["crs"] + rep["reference"])
    except Exception:
        rep["ok"] = False
        rep["error"] = traceback.format_exc()
    rep["elapsed_s"] = round(time.time() - t0, 1)
    Path(report_path).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if rep["ok"] else 1


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 (Windows 전용)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ================================================================ 화면
class QueueLog(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, rec):
        self.q.put(("log", rec.levelno, self.format(rec)))


class App:
    def __init__(self, config=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = tk.Tk()
        self.root.title(f"SORI v{__version__} — 도로교통소음 예측 (RLS-19 + ISO 9613-2)")
        self.root.geometry("860x640")
        self.root.minsize(720, 520)
        self.q = queue.Queue()
        self.worker = None
        self.cfg = tk.StringVar()
        self.out = tk.StringVar()
        self.t_grid = tk.BooleanVar(value=True)
        self.t_facade = tk.BooleanVar(value=True)
        self.t_points = tk.BooleanVar(value=True)
        self.do_plots = tk.BooleanVar(value=True)
        self.period = tk.StringVar(value=list(PERIODS)[0])
        self.status = tk.StringVar(value="설정 파일(config.yaml)을 고르거나 '예제 불러오기'를 누르세요.")
        self._build()
        h = QueueLog(self.q)
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False            # 창 실행(--windowed)에서는 표준 출력이 없을 수 있음 → 화면 기록창으로만
        log.info(AI_NOTICE)
        self._restore(config)
        self.root.after(100, self._poll)

    def _restore(self, config):
        """--config 로 받은 설정 파일, 없으면 지난번에 쓴 설정 파일을 미리 선택"""
        st = load_settings()
        p = Path(config) if config else (Path(st["last_config"]) if st.get("last_config") else None)
        if p and p.is_file():
            if not config and st.get("last_out"):
                self.out.set(st["last_out"])
            self.set_cfg(p.resolve())
            log.info("설정 파일: %s", p)

    def _build(self):
        tk, ttk = self.tk, self.ttk
        f = ttk.Frame(self.root, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="설정 파일").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.cfg).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="찾아보기…", command=self.pick_cfg).grid(row=0, column=2)
        ttk.Button(f, text="예제 불러오기", command=self.load_example).grid(row=0, column=3, padx=(6, 0))
        ttk.Label(f, text="결과 저장 폴더").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(f, textvariable=self.out).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="찾아보기…", command=self.pick_out).grid(row=1, column=2)

        opt = ttk.LabelFrame(f, text="계산 대상 (계산은 주간·야간 모두 수행)", padding=8)
        opt.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 6))
        ttk.Checkbutton(opt, text="격자(소음지도)", variable=self.t_grid).pack(side="left")
        ttk.Checkbutton(opt, text="건물 벽면(파사드)", variable=self.t_facade).pack(side="left", padx=10)
        ttk.Checkbutton(opt, text="점 수음점(설정에 있을 때)", variable=self.t_points).pack(side="left")
        ttk.Checkbutton(opt, text="그림·3D 뷰어 만들기", variable=self.do_plots).pack(side="left", padx=10)

        row = ttk.Frame(f)
        row.grid(row=3, column=0, columnspan=4, sticky="ew")
        ttk.Label(row, text="결과 요약 시간대").pack(side="left")
        ttk.Combobox(row, textvariable=self.period, values=list(PERIODS), state="readonly", width=20).pack(
            side="left", padx=6)
        self.btn_run = ttk.Button(row, text="▶ 계산 실행", command=self.start)
        self.btn_run.pack(side="left", padx=(16, 6))
        ttk.Button(row, text="결과 폴더 열기", command=self.open_out).pack(side="left")
        self.btn_check = ttk.Button(row, text="자가 점검", command=self.start_check)
        self.btn_check.pack(side="left", padx=6)

        self.bar = ttk.Progressbar(f, mode="determinate", maximum=1.0)
        self.bar.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(10, 2))
        ttk.Label(f, textvariable=self.status).grid(row=5, column=0, columnspan=4, sticky="w")

        box = ttk.Frame(f)
        box.grid(row=6, column=0, columnspan=4, sticky="nsew", pady=(6, 0))
        f.rowconfigure(6, weight=1)
        self.text = tk.Text(box, height=18, wrap="word", state="disabled")
        sb = ttk.Scrollbar(box, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.text.tag_configure("warn", foreground="#b35c00")
        self.text.tag_configure("err", foreground="#c00000")
        ttk.Label(f, text=f"※ {AI_NOTICE} 계산 결과의 검토·검증 책임은 사용자에게 있음.",
                  foreground="#666").grid(row=7, column=0, columnspan=4, sticky="w", pady=(6, 0))

    # ---------------------------------------------------------------- 버튼
    def pick_cfg(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(title="설정 파일 선택", filetypes=[("SORI 설정", "*.yaml *.yml"), ("모든 파일", "*")])
        if p:
            self.set_cfg(Path(p))

    def set_cfg(self, p):
        self.cfg.set(str(p))
        if not self.out.get():
            self.out.set(str(p.parent / "output"))
        self.status.set("설정 파일을 골랐습니다. 결과 저장 폴더를 확인하고 '계산 실행'을 누르세요.")

    def pick_out(self):
        from tkinter import filedialog
        p = filedialog.askdirectory(title="결과 저장 폴더 선택", mustexist=False)
        if p:
            self.out.set(p)

    def load_example(self):
        """앱에 들어 있는 기준 사례(가상 교차로, 실제 지역 아님)를 문서/SORI 아래에 복사해 선택"""
        dst = default_output_dir() / "예제_가상교차로"
        if not (dst / "config.yaml").exists():
            shutil.copytree(resource_path("tests", "reference", "synthetic", "input"), dst, dirs_exist_ok=True)
        self.out.set(str(dst / "output"))
        self.set_cfg(dst / "config.yaml")
        log.info("예제(가상 교차로, 실제 지역 아님): %s", dst)

    def open_out(self):
        p = Path(self.out.get() or default_output_dir())
        if p.exists():
            open_folder(p)
        else:
            self._msg("결과 폴더가 아직 없습니다.", err=True)

    def _msg(self, text, err=False):
        from tkinter import messagebox
        (messagebox.showerror if err else messagebox.showinfo)("SORI", text, parent=self.root)

    def _busy(self, on):
        st = "disabled" if on else "normal"
        self.btn_run.configure(state=st)
        self.btn_check.configure(state=st)

    def start(self):
        from tkinter import messagebox
        cfg, out = self.cfg.get().strip(), self.out.get().strip()
        if not cfg or not Path(cfg).is_file():
            return self._msg("설정 파일(config.yaml)을 먼저 고르세요.", err=True)
        if not out:
            return self._msg("결과 저장 폴더를 지정하세요.", err=True)
        which = [w for w, v in (("grid", self.t_grid), ("facade", self.t_facade), ("points", self.t_points)) if v.get()]
        if not which:
            return self._msg("계산 대상을 하나 이상 고르세요.", err=True)
        if Path(out).is_dir() and any(Path(out).iterdir()):
            if not messagebox.askyesno("SORI", f"결과 폴더에 이미 파일이 있습니다.\n{out}\n같은 이름의 결과 파일을 덮어쓸까요?",
                                       parent=self.root):
                return
        self._busy(True)
        self.bar.configure(value=0)
        self.status.set("계산 중… (창을 닫지 마세요)")
        save_settings(last_config=Path(cfg).resolve(), last_out=Path(out).resolve())
        args = (cfg, out, which, self.do_plots.get(), PERIODS[self.period.get()])
        self.worker = threading.Thread(target=self._work, args=args, daemon=True)
        self.worker.start()

    def _work(self, cfg, out, which, plots, period):
        def prog(label, done, total, eta):
            self.q.put(("prog", done / max(total, 1), f"{label} 계산 {done:,}/{total:,} 지점 · 남은 시간 약 {eta / 60:.1f}분"))
        try:
            r = run_job(cfg, out, which, plots, period, progress=prog)
            self.q.put(("done", f"계산 완료 ({r['elapsed_s'] / 60:.1f}분). 결과: {out}"))
        except Exception as e:
            log.error("오류: %s\n%s", e, traceback.format_exc())
            self.q.put(("fail", f"계산 중 오류: {e}"))

    def start_check(self):
        self._busy(True)
        self.bar.configure(mode="indeterminate")
        self.bar.start(15)
        self.status.set("자가 점검 중… (좌표 변환 + 기준 사례 계산, 1~2분)")
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        rp = default_output_dir() / "selfcheck_report.json"
        rp.parent.mkdir(parents=True, exist_ok=True)
        code = selfcheck(rp)
        rep = json.loads(rp.read_text(encoding="utf-8"))
        if code == 0:
            self.q.put(("done", f"자가 점검 통과 — 기준 사례 최대 차이 {rep['max_diff_dB']:.2g} dB "
                                f"(허용 0.01 dB, 프로젝트 임의 설정). 보고서: {rp}"))
        else:
            log.error("자가 점검 실패: %s", rep.get("error") or [r for r in rep.get("crs", []) + rep.get("reference", [])
                                                           if not r["ok"]])
            self.q.put(("fail", f"자가 점검 실패. 보고서: {rp}"))

    def _poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == "log":
                    tag = "err" if m[1] >= logging.ERROR else ("warn" if m[1] >= logging.WARNING else None)
                    self.text.configure(state="normal")
                    self.text.insert("end", ("⚠ " if tag else "") + m[2] + "\n", tag or ())
                    self.text.see("end")
                    self.text.configure(state="disabled")
                elif m[0] == "prog":
                    self.bar.configure(value=m[1])
                    self.status.set(m[2])
                else:
                    self.bar.stop()
                    self.bar.configure(mode="determinate", value=1.0 if m[0] == "done" else 0)
                    self.status.set(m[1])
                    self._busy(False)
                    if m[0] == "fail":
                        self._msg(m[1] + "\n자세한 내용은 아래 기록을 확인하세요.", err=True)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def run(self):
        self.root.mainloop()


def main(argv=None):
    argv = [a for a in (sys.argv[1:] if argv is None else argv) if not a.startswith("-psn_")]  # macOS 실행 인자 무시
    if argv[:1] == ["--selfcheck"]:
        rp = Path(argv[1] if len(argv) > 1 else "sori_selfcheck.json").resolve()
        logging.basicConfig(level=logging.INFO, filename=str(rp.with_suffix(".log")), encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(message)s")
        return selfcheck(rp)
    if argv[:1] == ["--run"]:
        import argparse
        ap = argparse.ArgumentParser(prog="sori_gui --run")
        ap.add_argument("config")
        ap.add_argument("--out", required=True)
        ap.add_argument("--no-plots", action="store_true")
        a = ap.parse_args(argv[1:])
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, filename=str(out / "sori_run.log"), encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(message)s")
        run_job(a.config, out, do_plots=not a.no_plots)
        return 0
    cfg = argv[1] if argv[:1] == ["--config"] and len(argv) > 1 else None
    App(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
