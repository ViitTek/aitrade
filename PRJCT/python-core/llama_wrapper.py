import os
import re
import subprocess
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT_DIR.parent
LLM_ROOT = REPO_ROOT / "LLM"
ENV_PATH = Path(__file__).resolve().with_name(".env")
load_dotenv(dotenv_path=ENV_PATH, override=False)

PROFILE_MODE_FILE = Path(os.getenv("LLAMA_PROFILE_MODE_FILE", str(Path(__file__).resolve().with_name("llm_profile_mode.txt"))))


def _existing_path(candidates: list[str]) -> str:
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return candidates[0] if candidates else ""


def _read_profile_mode() -> str:
    mode = "WORK"
    try:
        if PROFILE_MODE_FILE.exists():
            raw = PROFILE_MODE_FILE.read_text(encoding="utf-8").strip().upper()
            if raw in ("WORK", "PERF"):
                mode = raw
    except Exception:
        pass
    return mode


def _to_bool(v: str, default: bool = True) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return default


def _runtime_config() -> Dict[str, object]:
    llama_cli = _existing_path([
        os.getenv("LLAMA_CLI_PATH", ""),
        str(LLM_ROOT / "llama" / "llama-cli.exe"),
        r"C:\aiinvest\LLM\llama\llama-cli.exe",
        r"C:\AIInvest\LLM\llama\llama-cli.exe",
    ])

    model_path = _existing_path([
        os.getenv("LLAMA_MODEL_PATH", ""),
        str(LLM_ROOT / "models" / "mistral-7b-instruct-v0.2.Q4_K_M.gguf"),
        r"C:\aiinvest\LLM\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf",
        r"C:\AIInvest\LLM\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf",
    ])

    mode = _read_profile_mode()
    if mode == "PERF":
        ctx = _int_env("LLAMA_PERF_CTX_SIZE", _int_env("LLAMA_CTX_SIZE", 1024))
        batch = _int_env("LLAMA_PERF_BATCH_SIZE", _int_env("LLAMA_BATCH_SIZE", 128))
        ubatch = _int_env("LLAMA_PERF_UBATCH_SIZE", _int_env("LLAMA_UBATCH_SIZE", 64))
        threads = _int_env("LLAMA_PERF_THREADS", _int_env("LLAMA_THREADS", 10))
    else:
        ctx = _int_env("LLAMA_WORK_CTX_SIZE", _int_env("LLAMA_CTX_SIZE", 768))
        batch = _int_env("LLAMA_WORK_BATCH_SIZE", _int_env("LLAMA_BATCH_SIZE", 96))
        ubatch = _int_env("LLAMA_WORK_UBATCH_SIZE", _int_env("LLAMA_UBATCH_SIZE", 48))
        threads = _int_env("LLAMA_WORK_THREADS", _int_env("LLAMA_THREADS", 6))

    return {
        "llama_cli": llama_cli,
        "model_path": model_path,
        "ctx": ctx,
        "batch": batch,
        "ubatch": ubatch,
        "threads": threads,
        "no_repack": _to_bool(os.getenv("LLAMA_NO_REPACK", "1"), True),
        "mode": mode,
    }


def _build_base_cmd(max_tokens: int, temp: str) -> list[str]:
    cfg = _runtime_config()
    cmd = [
        str(cfg["llama_cli"]),
        "-m", str(cfg["model_path"]),
        "--n-predict", str(max_tokens),
        "--temp", temp,
        "--repeat-penalty", "1.1",
        "--ctx-size", str(cfg["ctx"]),
        "--batch-size", str(cfg["batch"]),
        "--ubatch-size", str(cfg["ubatch"]),
        "--threads", str(cfg["threads"]),
    ]
    if bool(cfg["no_repack"]):
        cmd.append("--no-repack")
    return cmd

def run_llama_oneword(prompt: str, timeout_sec: int = 25) -> str:
    cfg = _runtime_config()
    if not os.path.exists(str(cfg["llama_cli"])):
        raise FileNotFoundError("llama-cli.exe not found")
    if not os.path.exists(str(cfg["model_path"])):
        raise FileNotFoundError("Model file not found")

    # 4 tokeny stačí na "Positive"
    max_tokens = 4

    cmd = _build_base_cmd(max_tokens=max_tokens, temp="0")

    # Některé buildy jedou jako REPL: pošleme prompt + /exit
    full_input = prompt.strip() + "\n/exit\n"

    p = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8"
    )

    try:
        stdout, stderr = p.communicate(full_input, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        p.kill()
        stdout, stderr = p.communicate()
        raise RuntimeError(f"llama-cli timeout after {timeout_sec}s")

    if p.returncode != 0:
        raise RuntimeError(stderr.strip() or "llama-cli failed")

    # Vytáhni poslední výskyt jednoho z povolených slov
    matches = re.findall(r"\b(Positive|Neutral|Negative)\b", stdout, re.IGNORECASE)
    return matches[-1].capitalize() if matches else "Unknown"



def run_llama_structured(prompt: str, max_tokens: int = 150, timeout_sec: int = 60) -> str:
    """Spustí llama-cli s delším výstupem pro strukturovanou analýzu.
    Vrací raw text (volající parsuje).
    """
    cfg = _runtime_config()
    if not os.path.exists(str(cfg["llama_cli"])):
        raise FileNotFoundError("llama-cli.exe not found")
    if not os.path.exists(str(cfg["model_path"])):
        raise FileNotFoundError("Model file not found")

    full_input = prompt.strip() + "\n/exit\n"

    # Retry strategy for slower CPUs:
    # 1) requested max_tokens + timeout
    # 2) lower max_tokens, longer timeout
    attempts = [
        (max_tokens, timeout_sec),
        (max(48, int(max_tokens * 0.6)), int(timeout_sec * 1.5)),
    ]
    last_err = None

    for n_tokens, t_out in attempts:
        cmd = _build_base_cmd(max_tokens=n_tokens, temp="0.1")
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            stdout, stderr = p.communicate(full_input, timeout=t_out)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            last_err = RuntimeError(f"llama-cli timeout after {t_out}s")
            continue

        if p.returncode != 0:
            last_err = RuntimeError(stderr.strip() or "llama-cli failed")
            continue

        parsed = extract_response(stdout)
        if parsed:
            return parsed
        last_err = RuntimeError("llama-cli returned empty response")

    raise last_err or RuntimeError("llama-cli failed")


def extract_response(output: str) -> str:
    lines = output.splitlines()

    response_lines = []
    recording = False

    for line in lines:
        l = line.strip()

        if not l:
            continue

        if l.startswith(">"):
            recording = True
            continue

        if l.lower().startswith("exiting"):
            break

        if recording:
            response_lines.append(l)

    return "\n".join(response_lines).strip()




if __name__ == "__main__":
    prompt = (
        "Return exactly ONE word: Positive, Neutral, or Negative.\n"
        "Text: Nvidia stock surges after earnings beat.\n"
        "Answer:"
    )
    print("MODEL RESPONSE:")
    print(run_llama_oneword(prompt))
