@echo off
chcp 65001 >nul
title Solana localnet 종료
wsl -d Ubuntu -- sh -c "pkill -f solana-test-validator && echo OK: 검증기를 종료했습니다. || echo 실행 중인 검증기가 없습니다."
echo WSL 가상머신을 내려 램을 회수합니다...
wsl --shutdown
echo 완료. 램이 회수되었습니다 (지갑/토큰 데이터는 디스크에 안전하게 보관됨).
echo.
pause
