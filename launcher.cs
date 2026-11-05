using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Kerr studio")]
[assembly: AssemblyProduct("Kerr studio")]
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
            "assets/Kerr studio.ico",
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
                Console.WriteLine("Kerr studio package OK");
                return 0;
            }
            Console.Error.WriteLine("Missing Kerr studio runtime file: " + missing);
            return 2;
        }
        try
        {
            if (missing != null)
            {
                throw new FileNotFoundException("Extract the complete Kerr studio folder before opening Kerr studio.exe.", missing);
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
            MessageBox.Show(error.Message, "Kerr studio could not start", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
