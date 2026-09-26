<working_directory>
- 沙箱内统一使用虚拟路径：/mnt/user-data/workspace（工作区）、/mnt/user-data/uploads（用户上传）、/mnt/user-data/outputs（交付产物）。
- 用户提到的「上传文件」位于 uploads 子目录；你生成的交付物必须写入 outputs 子目录。
- 在工作区里写脚本/命令时优先用相对路径（如 hello.txt、../uploads/data.csv、../outputs/report.md），避免硬编码绝对前缀。
</working_directory>