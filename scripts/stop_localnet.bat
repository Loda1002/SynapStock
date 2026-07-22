@echo off
chcp 65001 >nul
title Solana localnet 종료
wsl -d Ubuntu -- sh -c "pkill -f solana-test-validator && echo OK: 검증기를 종료했습니다. || echo 실행 중인 검증기가 없습니다."
echo.
pause
