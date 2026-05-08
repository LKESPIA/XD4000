# LK XD4000 Basic Modbus TCP Tester

This is a simplified first-stage validation project focused only on XD4000 / ATV930 over Modbus TCP/IP.

## Purpose

Use this build to validate only:

1. Modbus TCP connection
2. Parameter upload/read
3. One controlled parameter download/write using TTO only

After this basic test is successful, the parameter list can be expanded.

## Minimal parameter list

Read-only upload test:

- ETA @ 3201
- RFR @ 3202
- FRH @ 3203
- LCR @ 3204
- ULN @ 3207
- THD @ 3209

Controlled write test after read test succeeds:

- TTO @ 6005, scale 0.1 s, range 0.1 to 30.0 s

Do not write CMD or LFR during first testing.

## GitHub build

Upload the full repository structure to GitHub, then run:

Actions -> Build XD4000 Basic Windows EXE -> Run workflow

Download artifact:

LK_XD4000_Basic_ModbusTCP_Tester-Windows-EXE

## First test sequence

1. Run EXE
2. Drive IP = actual drive IP, e.g. 192.168.1.10
3. Port = 502
4. Unit ID = 1
5. Keep zero-based address unchecked initially
6. Click Connect
7. Search 3201
8. Click Upload visible parameters
9. If Illegal Address occurs, tick zero-based address and reconnect, then repeat

## Safety

Use on bench setup first. Do not write CMD or LFR in the first test.
