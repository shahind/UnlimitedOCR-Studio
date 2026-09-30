// UnlimitedOCR-Studio.exe: runs Setup on first use, then starts the GUI with the Python that Setup
// selected (saved in python_path.txt). Compiled by build_windows.py with the C# compiler built into Windows.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Unlimited-OCR Studio")]
[assembly: System.Reflection.AssemblyProduct("Unlimited-OCR Studio")]

static class Launcher
{
    const string Title = "Unlimited-OCR Studio";
    static string Dir { get { return AppDomain.CurrentDomain.BaseDirectory; } }

    // bundle layout: app\ next to the exe; source checkout: exe next to app.py
    static string AppDir { get { return File.Exists(Path.Combine(Dir, "app.py")) ? Dir : Path.Combine(Dir, "app"); } }

    static string Python()
    {
        string f = Path.Combine(AppDir, "python_path.txt");
        if (!File.Exists(f)) return null;
        string py = File.ReadAllText(f).Trim();
        return File.Exists(py) ? py : null;
    }

    static string Quote(string s) { return "\"" + s.Replace("\"", "\\\"") + "\""; }

    [STAThread]
    static int Main(string[] args)
    {
        string py = Python();
        if (py == null)
        {
            var answer = MessageBox.Show(
                "Unlimited-OCR Studio needs a one-time setup.\n\n" +
                "Setup uses a Python already installed on this PC (preferring one that has CUDA PyTorch) " +
                "and installs only the missing parts. It shows what will be downloaded and your free " +
                "space before downloading anything (up to ~3 GB for CUDA PyTorch if missing, plus the " +
                "6.3 GB OCR model).\n\nRun setup now?",
                Title, MessageBoxButtons.YesNo, MessageBoxIcon.Information);
            if (answer != DialogResult.Yes) return 1;
            var setup = Process.Start(new ProcessStartInfo(Path.Combine(Dir, "Setup.bat"))
                { UseShellExecute = true, WorkingDirectory = Dir });
            setup.WaitForExit();
            py = Python();
            if (py == null)
            {
                MessageBox.Show("Setup did not complete. Run Setup.bat again to retry.",
                    Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return 1;
            }
        }
        string pyw = Path.Combine(Path.GetDirectoryName(py), "pythonw.exe");
        string arguments = Quote(Path.Combine(AppDir, "app.py"));
        foreach (string a in args) arguments += " " + Quote(a);
        Process.Start(new ProcessStartInfo(File.Exists(pyw) ? pyw : py, arguments)
            { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = AppDir });
        return 0;
    }
}
