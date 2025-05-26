@echo off
:: TODO: 未测试
:: 提取库名和版本号
set input=%1
for %%F in (%input%) do set filename_no_ext=%%~nF
for /f "tokens=1,2 delims=-" %%a in ("%filename_no_ext%") do (
    set library=%%a
    set version=%%b
)
if "%version%"=="" set version=latest

echo Library Name: %library%
echo Version: %version%

docker build -f "%1" -t test_%library%:%version% .

for /f %%i in ('docker create test_%library%:%version%') do set container_id=%%i


docker cp %container_id%:/root/output .
docker cp %container_id%:/root/resource .
docker rm %container_id%
echo Test Image: test_%library%:%version%