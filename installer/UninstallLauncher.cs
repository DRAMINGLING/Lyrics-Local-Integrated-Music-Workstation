using System;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using System.Windows.Forms;

internal static class UninstallLauncher
{
    [STAThread]
    private static int Main()
    {
        string appDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string[] candidates;

        try
        {
            candidates = Directory.GetFiles(appDirectory, "unins*.exe", SearchOption.TopDirectoryOnly);
            Array.Sort(candidates, delegate(string left, string right)
            {
                return File.GetLastWriteTimeUtc(right).CompareTo(File.GetLastWriteTimeUtc(left));
            });
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                "无法查找 Lyrics 卸载程序。\n\n" + exception.Message,
                "卸载 Lyrics",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }

        foreach (string candidate in candidates)
        {
            if (!Regex.IsMatch(Path.GetFileName(candidate), @"^unins[0-9]+\.exe$", RegexOptions.IgnoreCase))
                continue;

            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = candidate,
                    WorkingDirectory = appDirectory,
                    UseShellExecute = true
                });
                return 0;
            }
            catch (Exception exception)
            {
                MessageBox.Show(
                    "无法启动 Lyrics 卸载程序。\n\n" + exception.Message,
                    "卸载 Lyrics",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }
        }

        MessageBox.Show(
            "未找到 Lyrics 卸载程序。请从 Windows“已安装的应用”中卸载 Lyrics。",
            "卸载 Lyrics",
            MessageBoxButtons.OK,
            MessageBoxIcon.Error);
        return 1;
    }
}
