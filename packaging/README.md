# Atlas Windows 打包

运行 packaging/build_release.ps1。脚本会构建前端、创建隔离的打包虚拟环境，并生成 release/Atlas 目录。
随后用 Inno Setup 的 ISCC.exe 编译 release/Atlas.iss。
发行版不需要用户安装 Python、Node 或 npm。程序只监听 127.0.0.1:8000，数据默认保存到 %LOCALAPPDATA%\\Atlas。
