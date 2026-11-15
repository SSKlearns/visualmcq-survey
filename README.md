# VisualMCQ human evaluation

A minimal annotation server for the human-accuracy row of the VisualMCQ tables. Annotators log in, choose a
dataset and answer hard-bank questions (query image + candidate images, pick the same implant model / person).
The admin page shows progress and downloads every response as CSV.

## Start it

```bash
./run.sh
```

This (1) copies the hard-bank questions and the images they use from `../cvpr-27` into `data/` (first run only,
~220 MB), (2) builds the Docker image `vmcq-human-eval`, (3) (re)starts the container on **port 8015**.
Open `http://<server>:8015`.

- Other repo location: `VMCQ_SRC=/path/to/cvpr-27 ./run.sh`
- Other port: `PORT=9000 ./run.sh` (the container always listens on 8015 inside)
- If `docker` needs root on the machine, the script falls back to `sudo docker`.
- Proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, ...) are passed to the build when set.
- To deploy on another machine without the cvpr-27 repo, copy this folder **including `data/`** and run `./run.sh`.

## Accounts

`config/users.json`, plain strings. Edit names, passwords and annotator details (any extra field, e.g.
`specialty`, `experience_years`, appears in the CSV as `annotator_<field>`). The file is mounted into the
container and re-read at every login, so edits apply without a rebuild. Change the default passwords before
sharing the link.

## Responses

Stored in `state/responses.sqlite` on the host (mounted into the container). Rebuilding or restarting the
container never deletes them. Back up that file, or download the CSV from the admin page.

## How questions are chosen

- **Banks.** By default `implant_hard` and `reid_hard_cross`, asked as in the main results table: no "None of the
  above" option, and the questions whose answer is NOTA (62 implant, 11 people) are left out. For the NOTA table
  use `NOTA=1 REPREPARE=1 ./run.sh` (banks `implant_hard_nota`, `reid_hard_cross_nota`, NOTA option shown). Do
  not switch once annotation has started.
- **No overlap.** A question is handed to one annotator only (the question id is the primary key of the
  assignment table). An unanswered question stays with that annotator and is shown again when they return to
  the dataset.
- **Class balance.** Within a dataset the next question comes from the class (implant model / person) the
  annotator has seen least, then the class least seen by everyone, ties at random; within that class a patient
  the annotator has not seen yet is preferred. Any number of answers therefore stays close to balanced.
- Annotators get no feedback on correctness.

## CSV columns

One row per answered question: annotator (username, name, extra fields), domain, bank, dataset, split (query
set), qid, query image (path relative to the cvpr-27 repo), query class, patient / camera, for each candidate its
image, class and system rank, correct option and class, picked option and class, `is_correct`, NOTA flags,
system top-1, timestamps, seconds taken, and the annotator's sequence number. Option numbers are 1-based in
display order; with NOTA shown, NOTA is option k+1.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app: login, dataset choice, assignment, answers, admin, CSV |
| `templates/` | the five pages |
| `prepare_data.py` | builds `data/` from the cvpr-27 banks |
| `Dockerfile`, `run.sh` | image and one-command build + run |
| `config/users.json` | accounts |
