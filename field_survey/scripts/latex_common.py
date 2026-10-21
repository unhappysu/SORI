"""field_survey LaTeX 문서 공통 서식(XeLaTeX).

※ 본 스크립트는 생성형 AI(Claude)를 활용하여 작성되었음.

- A4 세로, 여백 22 mm, 본문 10.5 pt, 줄 간격 1.3, 제목 번호 1. / 1.1 / 1.1.1, 자동 목차
- 글꼴: Noto CJK KR 이 있으면 사용, 없으면 macOS 시스템 글꼴(AppleMyungjo·Apple SD Gothic Neo).
  고정폭은 TeX Live 의 DejaVu Sans Mono 파일
- 바닥글: 생성형 AI 활용 표기 + 쪽 번호(현재/전체)
- 표: longtable(쪽 넘김 시 머리글 반복), 입력 칸은 연노랑
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

AI_NOTE = "본 문서는 생성형 AI(Claude)를 활용하여 작성되었음"


def preamble(title, short, sans_body=False):
    body_font = (r"\renewcommand{\familydefault}{\sfdefault}"
                 r"\IfFontExistsTF{Noto Sans CJK KR}{\setmainhangulfont{Noto Sans CJK KR}}{\setmainhangulfont{Apple SD Gothic Neo}}"
                 if sans_body else "")                   # 현장 양식은 한글·영문 모두 고딕
    return r"""%% """ + title + r"""
%% 컴파일: xelatex (목차·전체 쪽수 반영을 위해 2회)
%% """ + AI_NOTE + r""".
\documentclass[10.5pt,a4paper]{article}
\usepackage[a4paper,top=22mm,bottom=24mm,left=22mm,right=22mm,headsep=6mm,footskip=10mm]{geometry}
\usepackage{fontspec}
\usepackage{xetexko}
\IfFontExistsTF{Noto Serif CJK KR}{%
  \setmainfont{Noto Serif CJK KR}\setsansfont{Noto Sans CJK KR}%
  \setmainhangulfont{Noto Serif CJK KR}\setsanshangulfont{Noto Sans CJK KR}%
  \setmonohangulfont{Noto Sans CJK KR}[Scale=0.85]}{%
  \setmainfont{Times New Roman}\setsansfont{Apple SD Gothic Neo}%
  \setmainhangulfont{AppleMyungjo}[AutoFakeBold=2.5]\setsanshangulfont{Apple SD Gothic Neo}%
  \setmonohangulfont{Apple SD Gothic Neo}[Scale=0.85]}
\IfFontExistsTF{DejaVu Sans Mono}{\setmonofont{DejaVu Sans Mono}[Scale=0.82]}{%
  \setmonofont{DejaVuSansMono.ttf}[Scale=0.82,BoldFont=DejaVuSansMono-Bold.ttf]}
""" + body_font + r"""
\usepackage{amsmath,amssymb}
\usepackage{array,booktabs,longtable,tabularx,makecell}
\usepackage[table]{xcolor}
\usepackage{graphicx}
\usepackage{enumitem}
\usepackage{needspace}
\usepackage{titlesec}
\usepackage{fancyhdr}
\usepackage{lastpage}
\usepackage[font=small,labelsep=colon,skip=4pt]{caption}
\usepackage[hidelinks]{hyperref}
\linespread{1.3}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.45em}
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.25}
\setlist{itemsep=0.15em,topsep=0.3em,leftmargin=1.6em}
\captionsetup[table]{name=표}
\captionsetup[figure]{name=그림}
\setcounter{secnumdepth}{3}
\setcounter{tocdepth}{2}
\definecolor{navy}{HTML}{1F3864}
\definecolor{headfill}{HTML}{D9E2F3}
\definecolor{inp}{HTML}{FFF8DC}
\definecolor{lab}{HTML}{F2F2F2}
\definecolor{team1}{HTML}{DDEBF7}
\definecolor{team2}{HTML}{E2EFDA}
\renewcommand{\thesection}{\arabic{section}.}
\renewcommand{\thesubsection}{\arabic{section}.\arabic{subsection}}
\renewcommand{\thesubsubsection}{\thesubsection.\arabic{subsubsection}}
\titleformat{\section}{\sffamily\Large\bfseries\color{navy}}{\thesection}{0.6em}{}
\titleformat{\subsection}{\sffamily\large\bfseries\color{navy}}{\thesubsection}{0.6em}{}
\titleformat{\subsubsection}{\sffamily\normalsize\bfseries}{\thesubsubsection}{0.6em}{}
\titlespacing*{\section}{0pt}{1.1em}{0.5em}
\titlespacing*{\subsection}{0pt}{0.9em}{0.4em}
\newcommand{\Laeq}{\ensuremath{L_{\mathrm{Aeq}}}}
\newcommand{\TBD}{\textcolor{gray}{[측정 후 기입]}}
\newcommand{\IN}{[\quad]}
\newcommand{\hd}[1]{\cellcolor{headfill}\textbf{\sffamily #1}}
\newcommand{\ip}{\cellcolor{inp}}
\newcommand{\lb}{\cellcolor{lab}}
\newcommand{\hr}[1]{\rule[-0.35\dimexpr#1\relax]{0pt}{#1}}
\newcommand{\notebox}[2][lab]{\par\noindent\colorbox{#1}{\parbox{\dimexpr\linewidth-2\fboxsep}{\small #2}}\par}
\renewcommand{\contentsname}{목차}
\pagestyle{fancy}\fancyhf{}
\fancyhead[L]{\small\sffamily """ + short + r"""}
\fancyhead[R]{\small\sffamily 전농사거리 소음현황조사}
\fancyfoot[L]{\scriptsize\sffamily\color{gray} """ + AI_NOTE + r"""}
\fancyfoot[R]{\small\sffamily \thepage\ / \pageref*{LastPage}}
\renewcommand{\headrulewidth}{0.4pt}
\fancypagestyle{plain}{\fancyhf{}\fancyfoot[L]{\scriptsize\sffamily\color{gray} """ + AI_NOTE + r"""}%
  \fancyfoot[R]{\small\sffamily \thepage\ / \pageref*{LastPage}}\renewcommand{\headrulewidth}{0pt}}
\begin{document}
"""


_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
        "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def esc(s):
    return "".join(_ESC.get(ch, ch) for ch in str(s))


def colspec(fracs, align=None):
    """열 너비 비율(합 1) → longtable 열 정의. align: 'l'/'c'/'r' 문자열"""
    out = []
    for i, f in enumerate(fracs):
        a = (align[i] if align else "l")
        ra = {"l": r"\raggedright", "c": r"\centering", "r": r"\raggedleft"}[a]
        out.append(r">{" + ra + r"\arraybackslash}p{\dimexpr " + f"{f:.4f}" + r"\linewidth-2\tabcolsep\relax}")
    return "".join(out)


def table(header, rows, fracs, align=None, caption=None, size=r"\small", height=None, fills=None, label=None, grid=False):
    """머리글 반복 표(longtable). 칸 문자열은 LaTeX 그대로 들어감(esc 는 호출하는 쪽에서).
    height: 본문 행 최소 높이(예: '10mm'). fills: {(r, c): '\\ip'} 같은 칸 앞 명령"""
    fills = fills or {}
    n = len(fracs)
    top, mid, bot = (r"\hline", r"\hline", r"\hline") if grid else (r"\toprule", r"\midrule", r"\bottomrule")
    head = " & ".join(r"\hd{" + h + "}" for h in header) + r" \\ " + mid
    spec = colspec([f * 0.985 for f in fracs] if grid else fracs, align)   # 세로선 두께만큼 줄임
    if grid:                                                     # 현장 양식: 세로선까지 그린 칸
        spec = "|" + spec.replace("}>{", "}|>{") + "|"
    lines = [size, r"\begin{longtable}{" + spec + "}"]
    if caption:
        lines.append(r"\caption{" + caption + "}" + (r"\label{" + label + "}" if label else "") + r"\\")
    lines += [top, head, r"\endfirsthead", top, head, r"\endhead", bot, r"\endlastfoot"]
    for ri, row in enumerate(rows):
        cells = []
        for ci in range(n):
            v = row[ci] if ci < len(row) else ""
            pre = fills.get((ri, ci), "")
            pre = pre + "{}" if pre else pre                    # \ip 뒤에 글자가 바로 붙어도 명령으로 읽히지 않게
            if ci == 0 and height:
                v = r"\hr{" + height + "}" + v
            cells.append(pre + v)
        lines.append(" & ".join(cells) + r" \\" + ((" " + mid) if ri < len(rows) - 1 else ""))
    lines += [r"\end{longtable}"]
    if not caption:
        lines.append(r"\addtocounter{table}{-1}")              # 제목 없는 표는 표 번호를 쓰지 않음
    lines.append(r"\normalsize")
    return "\n".join(lines) + "\n"


def items(lst):
    return "\\begin{itemize}\n" + "\n".join(r"\item " + x for x in lst) + "\n\\end{itemize}\n"


def compile_pdf(tex_path, out_pdf, runs=3):
    """임시 폴더에서 xelatex 를 runs 번 실행해 PDF 만 out_pdf 로 복사. 로그의 오류·경고 요약을 돌려줌"""
    tex_path, out_pdf = Path(tex_path), Path(out_pdf)
    with tempfile.TemporaryDirectory(prefix="fs_tex_") as d:
        work = Path(d) / "doc.tex"
        shutil.copy(tex_path, work)
        for f in tex_path.parent.glob("*.png"):         # 그림
            shutil.copy(f, Path(d) / f.name)
        for _ in range(runs):
            r = subprocess.run([shutil.which("xelatex") or "/Library/TeX/texbin/xelatex", "-interaction=nonstopmode", "-halt-on-error", "doc.tex"],
                               cwd=d, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                raise RuntimeError(f"xelatex 오류({tex_path.name}):\n" + r.stdout[-3000:])
        log = (Path(d) / "doc.log").read_text(errors="ignore")
        shutil.copy(Path(d) / "doc.pdf", out_pdf)
    keys = ("Overfull \\hbox", "Missing character", "undefined", "Rerun")
    res = {k: log.count(k) for k in keys}
    res["overfull_lines"] = [ln for ln in log.splitlines() if ln.startswith("Overfull")][:5]
    res["rerun_lines"] = [ln for ln in log.splitlines() if "Rerun" in ln][:3]
    return res
