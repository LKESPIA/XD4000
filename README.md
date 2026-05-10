# LK XD4000 Phase-2 Safety + Monitoring Tester

Phase-2 project for XD4000 / ATV930 Modbus TCP testing.

## What is included

- 22 controlled XD4000 parameters
- Upload/read validation
- Selected-row write
- Modified-parameter write
- FC06 write with FC16 fallback
- Retry and automatic readback after write
- Event log export
- Command Safety Test tab for status checks
- Raw CMD@8501 writes disabled in this safety build

## Build

Upload this project to GitHub and run:

Actions -> Build XD4000 Phase2 Windows EXE -> Run workflow

## Safety

Use on bench setup first. CMD raw command writes are disabled. LFR is writable for reference validation but should be used only in safe bench conditions.
