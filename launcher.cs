// Rebuild from this directory:
// %WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /target:winexe /out:TRPL.exe /win32icon:assets\TRPL.ico /reference:System.Windows.Forms.dll launcher.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
[assembly: AssemblyTitle("TRPL")]
[assembly: AssemblyProduct("Kerr Studio Desktop")]
[assembly: AssemblyVersion("0.1.1.0")]
internal static class Launcher
{
    [STAThread]
    private static int Main(string[] args)
    {
        string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string script = Path.Combine(root, "start-app.vbs");
        bool check = args.Length == 1 && args[0] == "--check";
        bool complete = File.Exists(script) && File.Exists(Path.Combine(root, "desktop.py"))
            && File.Exists(Path.Combine(root, "index.html"))
            && File.Exists(Path.Combine(root, "backend", "__main__.py"))
            && File.Exists(Path.Combine(root, "assets", "TRPL.ico"));
        if (check)
        {
            Console.WriteLine(complete ? "TRPL desktop package OK" : "TRPL runtime files are missing");
            return complete ? 0 : 2;
        }
        try
        {
            if (!complete) throw new FileNotFoundException("Extract the complete TRPL folder before opening TRPL.exe.");
            var start = new ProcessStartInfo();
            start.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "wscript.exe");
            start.Arguments = "//nologo \"" + script + "\"";
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            Process.Start(start);
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "TRPL could not start", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
