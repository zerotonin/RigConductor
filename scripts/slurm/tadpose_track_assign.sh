#!/usr/bin/env bash
#SBATCH --job-name=tadpose-assign
#SBATCH --partition=aoraki_gpu_L40,aoraki_gpu_A100_40GB,aoraki_gpu_A100_80GB,aoraki_gpu_L4_24GB,aoraki_gpu_RTX3090,aoraki_gpu_H100,aoraki_gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=06:00:00
#SBATCH --output=logs/tadpose-assign_%j.log
#
# Conductor tadpole backend: track one session's plate/well videos, run TadPose
# feature extraction and assign frames to the canonical k=36 prototypes.
#
#   sbatch scripts/slurm/tadpose_track_assign.sh SESSION_ID MODEL_RUN ENV
#
# Input : $PROJECT/conductor/in/SESSION_ID/*.mp4 + plate_map.json
# Output: $PROJECT/conductor/out/SESSION_ID/<video>_wells.csv, *_labels.csv,
#         prototype_proportions.csv (built by Conductor's score step locally)
#
# STATUS: scaffold. The tracker call (SLEAP 7-node skeleton vs DLC) and the
# tadpose assign entry point are pending the decisions in
# "Rig Conductor — Dev Plan" §4. Both branches below are written but not run.
set -euo pipefail
SESSION="${1:?session id}"
MODEL_RUN="${2:?model run name}"
ENV_NAME="${3:-sleap}"
PROJECT="${PROJECT:-$(pwd)}"
IN="$PROJECT/conductor/in/$SESSION"
OUT="$PROJECT/conductor/out/$SESSION"
mkdir -p "$OUT" logs

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "$ENV_NAME"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"

echo "session=$SESSION model=$MODEL_RUN env=$ENV_NAME node=$(hostname)"
nvidia-smi --query-gpu=name --format=csv,noheader || true

for vid in "$IN"/*.mp4; do
  base="$(basename "${vid%.mp4}")"
  case "$ENV_NAME" in
    sleap)
      # Caroline's 7-node skeleton: centre, left_eye, right_eye, tail1..3, tail_tip
      python -c "import sys; from sleap.nn.inference import main; main(sys.argv[1:])" \
        "$vid" -m "$PROJECT/models/$MODEL_RUN.centroid" -m "$PROJECT/models/$MODEL_RUN.centered_instance" \
        --tracking.tracker simple --tracking.similarity centroid -o "$OUT/$base.predictions.slp"
      python -m sleap.io.convert --format analysis "$OUT/$base.predictions.slp" -o "$OUT/$base.h5"
      ;;
    DEEPLABCUT)
      python - <<PY
import deeplabcut as dlc
dlc.analyze_videos("$PROJECT/single_tadpole-Bart-2023-11-23/config.yaml", ["$vid"],
                   destfolder="$OUT", save_as_csv=True)
PY
      ;;
  esac
done

# tadpose: wells geometry → ingest → features → assign to canonical prototypes
# (entry point per "Aoraki - Start New Analysis HOWTO": build_and_assign)
python -m tadpose.workflow build_and_assign --input "$OUT" --plate-map "$IN/plate_map.json" \
  --model-run "$MODEL_RUN" --output "$OUT" || {
  echo "tadpose assign failed or not available; see dev plan §4" >&2; exit 2; }
echo done
