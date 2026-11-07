' 静默启动 sdyxy 调度器(无命令行窗口): 双击运行即可
' 逻辑: 跑一次看门狗 -> 发现调度器没在跑就静默拉起(日志: output\scheduler.log)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\Desktop\auto-learn"
sh.Run """C:\Program Files\Python310\python.exe"" ""D:\Desktop\auto-learn\scripts\watchdog.py""", 0, False
