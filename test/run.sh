#!/bin/bash

# source test/cpp/run.sh <lib>
# example: source test/cpp/run.sh test/cpp/cereal-1.3.2.dockerfile

# 提取库名和版本号
input=$1
filename_no_ext=${input##*/}  # 移除最后一个 / 及其之前的部分
filename_no_ext=${filename_no_ext%.dockerfile}  # 移除 .dockerfile 扩展名
if [[ "$filename_no_ext" == *-* ]]; then
  library=${filename_no_ext%-*}
  version=${filename_no_ext#*-}
else
  library=$filename_no_ext
  version="latest"
fi

echo "Library Name: $library"
echo "Version: $version"

docker build -f "$1" -t test_"$library":"$version" .

container_id=$(docker create test_"$library":"$version")

docker cp "${container_id}":/root/output .
docker cp "${container_id}":/root/resource .

docker rm "${container_id}"
echo "Test Image: test_$library:$version"