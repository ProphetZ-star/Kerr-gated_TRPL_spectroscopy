using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("TRPL")]
[assembly: AssemblyProduct("Kerr TRPL Qt")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class Launcher
{
    [STAThread]
    private static int Main(string[] args)
    {
        string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        bool check = args.Length == 1 && string.Equals(args[0], "--check", StringComparison.OrdinalIgnoreCase);
        string[] required =
        {
            "desktop.py",
            "qt_frontend/window.py",
            "backend/worker.py",
            "assets/TRPL.ico",
            "start-app.vbs",
            "runtime_probe.py"
        };
        string missing = null;
        foreach (string relative in required)
        {
            string path = Path.Combine(root, relative);
            if (!File.Exists(path))
            {
                missing = path;
                break;
            }
        }
        if (check)
        {
            if (missing == null)
            {
                Console.WriteLine("TRPL Qt package OK");
                return 0;
            }
            Console.Error.WriteLine("Missing TRPL runtime file: " + missing);
            return 2;
        }
        try
        {
            if (missing != null)
            {
                throw new FileNotFoundException("Extract the complete TRPL folder before opening TRPL.exe.", missing);
            }
            string script = Path.Combine(root, "start-app.vbs");
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "wscript.exe");
            start.Arguments = "//nologo " + (char)34 + script + (char)34;
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
