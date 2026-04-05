## Vconverge Debug Task

### Problem
The vconverge run in `/workspace/GJ1132/XUV/Distributions/CumulativeXUV/Ribas/` failed. All 500 vplanet simulations completed successfully but vconverge recorded every run with exit code `-1`, resulting in zero successful runs and a non-zero exit status.

### Evidence

1. The `.output` file in the Ribas directory shows every run listed as `-1`:
```
/workspace/GJ1132/XUV/Distributions/CumulativeXUV/Ribas/output/xuv_rand_000 -1
/workspace/GJ1132/XUV/Distributions/CumulativeXUV/Ribas/output/xuv_rand_001 -1
...
```

2. However, the vplanet runs actually completed. For example, `output/xuv_rand_000/` contains:
   - `gj1132.b.forward` — has valid time-series data (Time, CumulativeXUVFlux)
   - `gj1132.star.forward` — exists
   - `gj1132.log` — exists with full output
   - All files have non-zero sizes

3. The vplanet output format looks correct:
```
0.000000 0.000000
1.000000e+06 1.270769e+16
...
6.414000e+09 1.126765e+18
```

4. The `vconverge_results.txt` file is empty — no convergence statistics were written.

5. The `vconverge.in` config is:
```
sVspaceFile vspace.in
iStepSize 100
iMaxSteps 200
sConvergenceMethod KS_statistic
fConvergenceCondition 0.004
iNumberOfConvergences 3
sObjectFile b.in
saConverge final CumulativeXUVFlux
```

### What to investigate

1. Read the vconverge source code at `/workspace/vconverge/`. Find where it determines whether a vplanet run succeeded or failed (the `-1` exit code).

2. The `-1` likely comes from vconverge's logic for checking run completion — it may be looking for a specific file, checking exit codes from multiplanet, or parsing output in a way that doesn't match the current vplanet output format.

3. Run a single vplanet simulation manually to confirm it works:
```bash
cd /workspace/GJ1132/XUV/Distributions/CumulativeXUV/Ribas/output/xuv_rand_000
vplanet vpl.in
echo $?
```

4. Check if multiplanet's checkpoint file is involved. Look for `.checkpoint` or `.lock` files in the output directory.

5. Once you identify why vconverge marks runs as `-1`, fix the bug in the vconverge source code at `/workspace/vconverge/`.

6. After fixing, test by re-running:
```bash
cd /workspace/GJ1132/XUV/Distributions/CumulativeXUV/Ribas
rm -rf output/xuv_rand_* vconverge_tmp vconverge_results.txt .output
vconverge vconverge.in
```

### Important constraints
- Do NOT modify vplanet C source code
- Do NOT modify the workflow JSON or vaibify code
- The fix should be in the vconverge Python package only
- Follow the style guide: Hungarian notation, camelCase filenames, functions prefixed with return type (fb, fs, fi, fn, flist, etc.), max 20 lines per function
- Run `pytest` in the vconverge directory after fixing to make sure tests pass
