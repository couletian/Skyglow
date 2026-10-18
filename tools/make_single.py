"""Assemble the single-file Skyglow page.

    python tools/make_single.py

Inputs (none of them modified):
    core/solar.py, spectrum.py, model.py, nn.py   -> already ported to _build/core.js
    data/model.json, data/nn_model.json           -> embedded as literals
    _build/part1.html, _build/part2.js            -> shell + app
Output:
    <workspace>/Skyglow-单文件版.html
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(os.path.dirname(HERE), "_build")
OUT = os.path.join(os.path.dirname(HERE), "Skyglow-单文件版.html")


def read(p, enc="utf-8"):
    with open(p, encoding=enc) as f:
        return f.read()


def main():
    shell = read(os.path.join(BUILD, "part1.html"))
    core = read(os.path.join(BUILD, "core.js"))
    app = read(os.path.join(BUILD, "part2.js"))

    model = json.loads(read(os.path.join(HERE, "data", "model.json")))
    nn = json.loads(read(os.path.join(HERE, "data", "nn_model.json")))

    # trim the parts of nn_model we never use, keeps the file small
    nn_slim = {k: nn[k] for k in ("version", "features", "mean", "std", "W", "B") if k in nn}

    banner = (
        "/* ==========================================================================\n"
        "   模型参数（与 data/model.json、data/nn_model.json 完全一致）\n"
        "   ridge-v2.1 / mlp-15-8-4-1\n"
        "   ========================================================================== */\n"
        "const MODEL = " + json.dumps(model, ensure_ascii=False) + ";\n"
        "const NN = " + json.dumps(nn_slim, ensure_ascii=False) + ";\n"
    )

    # core.js exports via module.exports; harmless in a browser, but drop the tail
    core_body = core.split("if (typeof module !== \"undefined\")")[0].rstrip() + "\n"

    out = []
    out.append(shell.rstrip())
    out.append("")
    out.append("<script>")
    out.append(banner)
    out.append(core_body)
    out.append("</script>")
    out.append("")
    out.append(app.strip())
    out.append("")

    html = "\n".join(out)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    size = os.path.getsize(OUT)
    print("wrote %s" % OUT)
    print("  size  : %.1f KB" % (size / 1024))
    print("  model : %s" % model["version"])
    print("  nn    : %s" % nn_slim.get("version"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
