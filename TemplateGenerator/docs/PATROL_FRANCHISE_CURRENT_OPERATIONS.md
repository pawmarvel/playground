# Patrol Franchise expansion — current-tool operating proposal

Status: proposed operating practice using existing commands; generated concept
inputs are drafts, not approved templates. Prepared 2026-10-04.

Use this alongside the [MVP operations guide](MVP_OPERATIONS_GUIDE.md).
The [optimized proposal](PATROL_FRANCHISE_OPTIMIZED_OPERATIONS.md) describes
future orchestration and must not be mistaken for available commands.

## 1. Scope and source of truth

The private workbook is
`work/design-inputs/Test Design Pool/patrol_franchise_revised_with_visual_concepts.xlsx`,
sheet **Revised Concept Matrix**. Its 23 data rows each have three bottom-line
options: **69 proposed input sets**, not 69 automatically approved products.
The Santa and Xmas Present Security rows remain distinct. Gift Patrol rows
also remain distinct by collection/concept. Pool folders use
`<concept-name>-<bottom-line-text>`, normalized to lowercase hyphenated names.
The duplicate Present Security concepts include the Santa/Xmas headline.
The readable folder name is also the `PAWMARVEL_DESIGN_ID`, so authoring paths,
configs, reviews, and later bundles remain immediately recognizable. The
index retains the former `-bl01`/`-bl02`/`-bl03` identifier only as
`legacy_design_id`; option numbers 1–3 still correspond to columns E–G.

The pool's `README.md`, `concept-index.json`, and `gallery.html` map folders and design IDs
to worksheet rows, exact copy, intended props, source bundle and draft status.
These private files are not FE schemas or tooling configuration. Do not pass
`concept-index.json` to a CLI that expects an experiment or fixture manifest.

The nine user-approved source designs were inspected under
`work/exchange/bundles/<template-id>/v000001`. The index pins their manifest,
prompt, layout and reference hashes. Source approval is supplied by the
application owner; a local bundle directory alone does not prove S3 publication
or FE activation. No source bundle is modified by this proposal.

## 2. Franchise rules learned from the approved assets

All nine manifests use `embedded-in-pet`, a 1008×1008 pet output, low-quality
OpenAI runtime generation, and the twin/full profile. Their model IDs differ:
some pin `gpt-image-2`, others `gpt-image-2.5-sunburst`. Those historical bundles
must retain their recorded models; new candidates use the current configured
Sunburst default and require fresh tests.

The shared visual system is:

- arched black display/slab-serif headline;
- one small black silhouette motif in the upper-right;
- dominant hand-inked pet, directional fur strokes and restrained colored
  shading, with concept-specific local props;
- casual handwritten pet name inside the pet asset;
- humorous bottom line and two footer paw prints.

The new prompts make the ownership boundary explicit:

| Reusable `art.png` | Personalized `transformed-pet.png` |
| --- | --- |
| Headline, upper-right motif, bottom line, two footer paws | Actual customer pet, worn accessories, center props, sparse local marks, supplied handwritten name |
| No pet, center props or name | No headline, corner motif, bottom line or footer paws |
| Actual alpha outside fixed artwork | Actual alpha outside the combined center artwork |

The proposed references deliberately use a white page for easy inspection. It
is **not** a production background. Both generated production layers must be
transparent. The sample pet/name (`Milo`) in a reference must never override
the customer's species, breed, coat, anatomy or supplied name.

Do not blindly copy the old source prompts. Some lock a screenshot's canvas
ratio, and some retain wording from another design. The new drafts instead
give the requested product dimensions priority, use this option's exact copy,
and explicitly exclude this option's fixed art from the pet output. They are
starting points requiring scratch testing, not proven runtime prompts.

## 3. Review inputs before spending on benchmarks

Open the local pool gallery. Review options side by side within each worksheet
row, then compare the shortlisted rows against the nearest approved source.
The initial reference images are separately generated illustrations: small
differences in pose/detail are not evidence that a slogan caused a preference.
For a controlled copy test, lock a single accepted composition and change only
the bottom line in a subsequent explicitly reviewed reference iteration.

Confirm:

1. Exact headline and bottom line, including punctuation and case.
2. Correct corner motif and center props, with no carry-over Christmas hat,
   holly or winter scene in Thanksgiving, Hanukkah, Diwali or neutral designs.
3. Readable long slogans, unclipped type, full pet anatomy and a clear name zone.
4. Familiar franchise style, not a generic cartoon or photographic pet.
5. Respectful holiday imagery; localize/review unfamiliar cultural details.
   Snack jokes do not need to depict ingestion, and lamps stay away from fur.

Mark a shortlist in a private operator note with design ID, date, reviewer and
reason. This is concept selection only; later `record-decision` records actual
artifact evaluations. Never fabricate experiment decisions from a concept image.

Suggested rollout follows workbook priority: Christmas, holiday-neutral,
Thanksgiving, Hanukkah, Diwali, Halloween, New Year, Birthday, Gotcha Day, Love.
Start with one bottom-line finalist per chosen row; keep all alternatives in
the pool. A small first batch of Santa, Cookie and Package Patrol tests reuse,
food props and a different sniffing pose without immediately benchmarking 69
variants. Later add a cultural-holiday pilot before expanding that collection.

For a small team vote, use `pawmarvel-gallery` instead of sharing the static
HTML file. It keeps the private inputs under ignored `work/`, collects one
updatable `Graduate` / `Improve` / `Abandon` vote plus comment per reviewer/design,
and exports JSON or CSV. The complete LAN and HTTPS-tunnel guidance is in the
[private gallery voting guide](GALLERY_VOTING_GUIDE.md). Concept votes only
create a shortlist; they do not replace immutable artifact decisions.

## 4. Stage one selected variant and initialize once

Each pool folder contains exactly:

```text
cookie-watch-cookies-under-surveillance/
  reference-design.png       # finished proposal, not reusable art.png
  art-template-gpt.md        # fixed-art candidate prompt
  pet-transform-gpt.md       # center cutout + {{PET_NAME}} candidate prompt
```

Stage a COPY into the current tool's standard input location. Keep the pool as
the concept-review source. Run this block once for a newly chosen design ID;
it resolves the readable pool folder from the index and refuses to overwrite
an existing working input folder. Copy the design ID shown in the gallery; it
matches the readable folder name:

```bash
PAWMARVEL_PROJECT="/Users/qbit/Documents/PawMarvel/Code/playground/TemplateGenerator"
PAWMARVEL_POOL="$PAWMARVEL_PROJECT/work/design-inputs/Test Design Pool"
PAWMARVEL_DESIGN_ID="cookie-watch-cookies-under-surveillance"
PAWMARVEL_PRODUCT_PROFILE_ID="blanket-twin-full-7875x9375"

stage_patrol_variant() {
  local source_dir
  source_dir="$("$PAWMARVEL_PROJECT/.venv/bin/python" - "$PAWMARVEL_POOL" "$PAWMARVEL_DESIGN_ID" <<'PY'
import json
import sys
from pathlib import Path

pool = Path(sys.argv[1]).resolve()
entries = json.loads((pool / "concept-index.json").read_text())["concepts"]
matches = [entry for entry in entries if entry["design_id"] == sys.argv[2]]
if len(matches) != 1:
    raise SystemExit(f"Expected one entry for {sys.argv[2]!r} in {pool / 'concept-index.json'}; found {len(matches)}")
print(pool / matches[0]["folder"])
PY
  )" || return 1
  local target_dir="$PAWMARVEL_PROJECT/work/design-inputs/$PAWMARVEL_DESIGN_ID"
  local filename
  for filename in reference-design.png art-template-gpt.md pet-transform-gpt.md; do
    test -f "$source_dir/$filename" || {
      printf 'Missing draft input: %s\n' "$source_dir/$filename" >&2
      return 1
    }
  done
  if test -e "$target_dir"; then
    printf 'Already exists; inspect and resume instead of overwriting: %s\n' "$target_dir" >&2
    return 1
  fi
  mkdir -p "$target_dir" || return 1
  for filename in reference-design.png art-template-gpt.md pet-transform-gpt.md; do
    cp "$source_dir/$filename" "$target_dir/$filename" || return 1
  done
}
stage_patrol_variant
```

Stop if staging reports an error. Do not continue using a different variant's
stale shell variables. A new bottom-line identity remains separate from the
existing approved Santa/Ghost/Porch products even if the headline is shared.

After verifying the staged copy, use the gallery's operator page to select
**Graduate** for the source concept. The server records the lifecycle event and
moves the complete folder from `Test Design Pool` to `Graduation Pool`. For an
unselected concept select **Abandon**; no staging copy is needed. Do not remove
the `concept-index.json` entry or move folders manually. Votes are retained
outside the pools for 30 days, and either outcome can be restored from the
operator page. See the [operator guide](DESIGN_REVIEW_OPERATOR_GUIDE.md) for
the exact review, reset, restore, and retention workflow.

Reuse the existing shared credentials config; if not yet created, follow the
main guide's shared configuration step first. Then:

```bash
source "$PAWMARVEL_PROJECT/work/configs/pawmarvel-shared.env"

if PAWMARVEL_CONFIG="$("$PAWMARVEL_PROJECT/.venv/bin/pawmarvel-author" init-config \
  --design-id "$PAWMARVEL_DESIGN_ID" \
  --product-profile-id "$PAWMARVEL_PRODUCT_PROFILE_ID" \
  --version-number 1 \
  --name-mode embedded-in-pet \
  --pet-name MILO \
  --pet-name-max-length 12 \
  --art-model gpt-image-2.5-sunburst \
  --art-quality high \
  --pet-model gpt-image-2.5-sunburst \
  --pet-quality low)"; then
  "${EDITOR:-vi}" "$PAWMARVEL_CONFIG" && source "$PAWMARVEL_CONFIG"
else
  printf 'Configuration was not created; correct the error before continuing.\n' >&2
fi
```

Review the profile, pet input, release identity and paths in this config. In a
returning session, source the existing config rather than recreate it. Use a
new config version for a changed baseline. Follow main-guide section 3.1 to
derive reference/name arrays and prompt validation, then sections 5–9.

Use the target concept reference as the primary reference. Do not automatically
append all nine approved designs: they conflict in copy, props and costume.
Only add a supporting reference for a specific unresolved style issue, explain
its role in the prompt, and keep the ordered reference list identical during
scratch and benchmark. The current runtime limit is four finished-design
references, in addition to the user pet.

## 5. Scratch gates: prove the split and personalization first

Art scratch follows main-guide section 5.1. It does not require a transformed
pet or layout yet. Keep editing the scratch prompt until only the four fixed
components remain. Inspect alpha on light and dark backgrounds; a white center
is not transparent. Inspect the long bottom line at preview size.

Pet scratch follows section 6.1, with `--pet-name` provided by the configured
embedded-name array and **quality low**, matching the intended runtime. Test
one or two contrasting pets first, then short/long names (for example `BO` and
`ALEXANDERMAXX`, 12 code points). A changed artistic name requires a new image
call; typing a different name into a renderer cannot change baked lettering.

Keep pet inputs, ordered references, model and quality fixed while revising
one prompt hypothesis. Different source breeds, a short tail, a dark coat, a
long body and cats must remain recognizable when those are in product scope.
Do not add a layout font/name layer over a generated artistic name.

Scratch images are disposable visual evidence, not immutable attempts. Promote
accepted prompt text to the usual candidate path and create fresh experiments.
Do not copy a generated proposal or scratch PNG into an attempt directory and
claim it came from `run-attempt`.

## 6. Durable development and manual checkpoints

Use the existing guide for the exact commands and decision-derived paths:

| Stage | Existing tool flow | Manual acceptance / expected artifact |
| --- | --- | --- |
| Art | `create-experiment --kind art`, `run-attempt`, `compare --kind art`, `record-decision` | Exact fixed copy, alpha and spacing; selected immutable art attempt |
| Pet smoke | `create-experiment --kind pet`, `prepare-benchmark`, inspect selection, `benchmark`, `compare --kind pet` | 2–3 pets, one attempt each; shortlist only |
| Pet release | Prepare/review an exact release selection, benchmark and compare it, record pet decision | Prefer 6–15 varied pets, one attempt each; current low-coverage warnings remain human decisions |
| Layout | Create layout experiment from the winning art and exact representative pet, open editor | Pet box only for embedded names; no `name` config or font assets; inspect alternative cutouts |
| Assembly | Compare the chosen layout and runtime, record layout and assembly reviews | No headline/footer collisions, pet identity preserved, readable actual embedded names |
| Print | `prepare-print` using the selected art/pet/layout reviews | Correct 7875×9375 result, edges and lettering reviewed; no fresh substitute pet |
| Graduation | `graduate` using exact print candidate and reviews | Application-owner acceptance of the combination |
| Package / release | `pawmarvel-bundle`, catalog build/validate, explicit S3 publish | Independent immutable revision; FE remains owner of activation |

Do not interpret one attempt per fixture as a stochastic reliability study.
Record failures, missing fixtures, latency and coverage warnings, not just
successful pictures. A slogan-only alternative changes the pet reference image
if its full finished reference changes; that is still a runtime input change
and requires fresh QA, even if the pet prompt text is nearly identical.

For name checks beyond the benchmark's chosen name, generate additional paid
probes with their actual names recorded. Keep those separate from fixture-count
claims. In the current UI, load alternative transformed PNGs to inspect the
same pet box; do not assume changing a text input regenerates embedded names.

Current automation limitations remain: there is no cross-concept runner or
automatic layout optimizer, and a parent layout is not an accepted child
layout. Use it only as a visual seed. If the selected art/pet changes, create a
successor layout experiment with the correct immutable input lineage.

## 7. Artifacts and FE handoff

```text
work/design-inputs/Test Design Pool/<concept>-<bottom-line>/  # 3 proposed inputs
work/design-inputs/<variant-id>/                   # working source copy
work/configs/<variant-id>--<profile-id>--v01.env
work/authoring/<variant-id>/<profile-id>/
  scratch/                                       # replaceable draft output
  experiments/{art,pet,layout}/                   # immutable evidence
  reviews/{art,pet,layout,assembly}/              # evaluation + decision
  print-candidates/                              # reviewed print combination
  graduations/                                   # selected product artifact
work/exchange/bundles/<variant-id>--<profile-id>/v000001/
work/exchange/releases/<release-id>/catalog.json
```

The FE receives only the ordinary validated bundle/release. It does not load
this workbook, pool, base bundle or concept index. For these candidates,
`renderer.name_mode` remains `embedded-in-pet`; the runtime prompt contains
`{{PET_NAME}}`, layout has no separate name layer, and the bundle's name policy
governs input validation. Preserve the existing reference ordering, alpha
normalization, preview/print geometry and selected model/quality contract.

Build releases only for accepted finalists. Publishing is separate from FE
activation, and a draft reference image is never a substitute for graduation.
Do not automatically republish all parent bundles with each new variant.

## 8. Iteration, retirement and cost control

Change one hypothesis at a time: copy/layout, art prompt, pet prompt, or model.
Within the same candidate identity, use successor experiment IDs and immutable
bundle revisions; never rewrite a released bundle. New copy alternatives may
keep their own design IDs, allowing independent catalog selection.

Keep accepted inputs and selection evidence. For rejected experiments, follow
main-guide section 13: mark eligible experiments discarded, preview cleanup,
then explicitly delete only intended work. Selected experiments are protected.
Do not apply authoring cleanup to the input pool, exchange or S3. Keep the
workbook/index and selected draft sets backed up privately; they are Git-ignored
and are not a backup merely because they are under the repository directory.

Budget per finalist, not per entire spreadsheet: scratch calls + art attempts
+ smoke fixtures + release fixtures + any artistic-name probes + retries and
optional paid upscale. The 69 reference images are concept-preparation work,
not included in later runtime latency benchmarks. Shortlisting first avoids
paying for release coverage on every rejected slogan.
