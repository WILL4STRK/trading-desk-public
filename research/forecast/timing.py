"""Section 16.1 item 6: seconds per forecast on SYNTHETIC series only (50 random walks of 288 steps, batch of one, 48-step
horizon, CPU). No real price series is read here. Run with the forecast venv: python research/forecast/timing.py"""
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import torch
from chronos import Chronos2Pipeline

REV = (Path(__file__).parent / "MODEL_REVISION").read_text().split()
pipe = Chronos2Pipeline.from_pretrained(REV[0], revision=REV[1], device_map="cpu")
rng = np.random.default_rng(16)
series = [torch.tensor(np.exp(np.cumsum(rng.normal(0, 0.01, 288))) * 0.001, dtype=torch.float32) for _ in range(50)]
pipe.predict_quantiles([series[0]], prediction_length=48, quantile_levels=[0.2, 0.5])   # warm-up, not timed
times = []
for s in series:
    t0 = time.perf_counter()
    q, _ = pipe.predict_quantiles([s], prediction_length=48, quantile_levels=[0.2, 0.5])
    times.append(time.perf_counter() - t0)
out = {"n_series": len(times), "median_s": statistics.median(times), "mean_s": statistics.fmean(times),
       "max_s": max(times), "torch_threads": torch.get_num_threads()}
print(json.dumps(out))
