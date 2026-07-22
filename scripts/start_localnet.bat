@echo off
chcp 65001 >nul
title Solana localnet (연습용 미니 네트워크)
echo ============================================================
echo  Solana localnet 검증기를 시작합니다.
echo.
echo  - 이 창이 떠 있는 동안 = 켜져 있는 것입니다.
echo  - 끄기 = 이 창을 닫거나 stop_localnet.bat 더블클릭.
echo  - 만들어 둔 지갑/토큰 데이터는 그대로 유지됩니다.
echo ============================================================
echo.
wsl -d Ubuntu -- sh -c "export PATH=$HOME/.local/share/solana/install/active_release/bin:$PATH; cd ~ && exec solana-test-validator"
echo.
echo 검증기가 종료되었습니다. 이 창은 닫아도 됩니다.
pause
