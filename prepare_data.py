#!/usr/bin/env python3
"""Copy the hard-bank questions and the images they use from the cvpr-27 repo into ./data.

    python3 prepare_data.py --src ../cvpr-27            # default: no NOTA option (main results table)
    python3 prepare_data.py --src ../cvpr-27 --nota     # the *_nota banks, with "None of the above"

Without --nota the banks are implant_hard and reid_hard_cross, asked the way the main table asks them: the
"None of the above" option is not shown and the few questions whose answer is NOTA (62 implant, 11 people) are
left out. With --nota the banks are implant_hard_nota and reid_hard_cross_nota and the NOTA option is shown.

Writes data/questions.jsonl (one normalised question per line) and data/images/<repo path>.
"""
import argparse, json, os, shutil, sys

DATASET_NAMES = {"ortho_hip": "Hip (OrthoNet)", "ortho_knee": "Knee (OrthoNet)",
                 "physionet": "Cardiac devices (PhysioNet)", "pacemakers": "Pacemakers",
                 "market1501": "People (Market-1501)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="../cvpr-27", help="root of the cvpr-27 repo")
    ap.add_argument("--out", default="data")
    ap.add_argument("--nota", action="store_true", help="use the *_nota banks and show the NOTA option")
    a = ap.parse_args()

    banks = ["implant_hard_nota", "reid_hard_cross_nota"] if a.nota else ["implant_hard", "reid_hard_cross"]
    out_q, n_img = [], 0
    for bank in banks:
        bdir = os.path.join(a.src, "banks", bank)
        info = json.load(open(os.path.join(bdir, "bank.json")))
        img_root = os.path.normpath(os.path.join(bdir, info["image_root"]))      # e.g. <src>/data/implants
        rel_root = os.path.relpath(img_root, a.src)                              # e.g. data/implants
        domain = info["domain"]
        for line in open(os.path.join(bdir, "questions.jsonl")):
            q = json.loads(line)
            correct = q["correct_option"]                                       # bank numbering: NOTA = 6
            imgs = [o for o in q["options"] if "image" in o]
            if not a.nota and correct == 6:
                continue                                                         # main table: NOTA-answer questions skipped
            people = domain != "implants"
            cls = "pid" if people else "model"
            opts = [{"image": f"{rel_root}/{o['image']}", "class": o[cls], "rank": o.get("rank")} for o in imgs]
            out_q.append({
                "uid": f"{bank}:{q['qid']}", "bank": bank, "qid": q["qid"], "domain": domain,
                "dataset": q["dataset"], "dataset_name": DATASET_NAMES.get(q["dataset"], q["dataset"]),
                "split": q["query_set"], "noun": info["noun"],
                "query_image": f"{rel_root}/{q['query_image']}",
                "query_class": q["query_pid"] if people else q["query_model"],
                "query_patient": q.get("query_patient"), "query_cam": q.get("query_cam"),
                "options": opts, "nota_offered": a.nota,
                "correct_option": (len(opts) + 1) if correct == 6 else correct,   # 1-based; k+1 = NOTA
                "nota_design": q.get("nota_design"), "retrieval_top1": q.get("retrieval_top1"),
            })
            for p in [out_q[-1]["query_image"]] + [o["image"] for o in opts]:
                dst = os.path.join(a.out, "images", p)
                if not os.path.exists(dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(os.path.join(a.src, p), dst)
                    n_img += 1
        print(f"{bank}: {sum(1 for x in out_q if x['bank'] == bank)} questions")

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "questions.jsonl"), "w") as f:
        for x in out_q:
            f.write(json.dumps(x) + "\n")
    json.dump({"banks": banks, "nota": a.nota, "n_questions": len(out_q)},
              open(os.path.join(a.out, "meta.json"), "w"), indent=1)
    print(f"wrote {len(out_q)} questions to {a.out}/questions.jsonl, copied {n_img} new images")


if __name__ == "__main__":
    sys.exit(main())
