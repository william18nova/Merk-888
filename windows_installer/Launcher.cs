using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// No elevated privileges, PowerShell bypasses, services, firewall or Defender changes.
class NovaWindow : Form {
    [DllImport("kernel32.dll")] static extern void GetNativeSystemInfo(out SYSTEM_INFO info);
    [StructLayout(LayoutKind.Sequential)] struct SYSTEM_INFO {
        public ushort architecture, reserved; public uint pageSize;
        public IntPtr minAddress, maxAddress, processorMask;
        public uint processors, processorType, granularity;
        public ushort level, revision;
    }
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
    readonly Dictionary<string, TextBox> fields = new Dictionary<string, TextBox>();
    readonly TextBox status = new TextBox { Multiline=true, ReadOnly=true, ScrollBars=ScrollBars.Vertical, Dock=DockStyle.Fill };
    readonly FlowLayoutPanel actions = new FlowLayoutPanel { Dock=DockStyle.Fill, AutoSize=true };
    readonly Button check = new Button { Text="1. Comprobar", AutoSize=true };
    readonly Button activate = new Button { Text="2. Vincular equipo", AutoSize=true, Enabled=false };
    readonly Button open = new Button { Text="Abrir NovaPOS", AutoSize=true };
    readonly Button stop = new Button { Text="Detener", AutoSize=true, Enabled=false };
    Process child;
    bool busy, closeWhenStopped;
    Dictionary<string, object> confirmed;
    Dictionary<string, object> checkedValues;

    [STAThread] static void Main(string[] args) {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        SYSTEM_INFO info; GetNativeSystemInfo(out info);
        if (Environment.OSVersion.Version.Major < 10 || info.architecture != 9 || !Environment.Is64BitProcess) {
            MessageBox.Show("NovaPOS requiere Windows 10/11 de 64 bits (x64). No se instalaron componentes.", "Equipo no compatible");
            return;
        }
        if (args.Length == 1 && args[0] == "--check-platform") return;
#if PACKAGE
        // Build verification can extract to a NEW disposable folder without running/configuring a DB.
        if (args.Length == 2 && args[0] == "--extract-only") { Extract(args[1]); return; }
#endif
        Application.Run(new NovaWindow());
    }

    NovaWindow() {
        Text="NovaPOS · Instalación y conexión";
        Font=new Font("Segoe UI", 10);
        AutoScaleMode=AutoScaleMode.Dpi;
        MinimumSize=new Size(650, 680); Size=new Size(820, 810);
        StartPosition=FormStartPosition.CenterScreen;
        var layout = new TableLayoutPanel { Dock=DockStyle.Fill, Padding=new Padding(24), ColumnCount=1, RowCount=4 };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.Controls.Add(new Label {Text="NovaPOS · Tu aplicación online y offline", AutoSize=true, Font=new Font("Segoe UI",18,FontStyle.Bold), Margin=new Padding(0,0,0,16)});
        Controls.Add(layout);
#if PACKAGE
        var info = new Label { AutoSize=true, MaximumSize=new Size(720,0), Text="Instala la aplicación completa, Python y un PostgreSQL privado. No necesitas instalarlos por separado.\r\n\r\nDespués debes vincular este computador con su propio usuario, caja y turno. No se copian credenciales ni ventas de otro equipo.\r\n\r\nNo se reemplazan instalaciones ni bases existentes. Si Windows bloquea un componente, conserva sus protecciones y solicita un paquete firmado compatible.\r\n\r\nDestino: " + InstallRoot() };
        layout.Controls.Add(info);
        var install = new Button { Text="Instalar NovaPOS", AutoSize=true, Margin=new Padding(0,20,0,20) };
        layout.Controls.Add(install); layout.Controls.Add(status);
        install.Click += async delegate {
            busy=true; install.Enabled=false;
            try {
                Log("Verificando y extrayendo componentes. Puede tardar varios minutos…");
                await Task.Run(() => Extract(InstallRoot()));
                Shortcuts(InstallRoot());
                Log("Instalación de archivos terminada. Se abrirá el asistente de vinculación.");
                Process.Start(new ProcessStartInfo(Path.Combine(InstallRoot(),"NovaPOS.exe")) {UseShellExecute=true});
            } catch (Exception) { Log("No se completó la instalación. Puede existir otra instalación, faltar espacio o haber un bloqueo de Windows. Los archivos se conservaron; no desactives las protecciones."); }
            finally { busy=false; install.Enabled=true; }
        };
#else
        var form = new TableLayoutPanel { AutoSize=true, Dock=DockStyle.Top, ColumnCount=3 };
        form.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize)); form.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        AddField(form,"Servidor de la nube","host","");
        AddField(form,"Puerto de la nube","port","");
        AddField(form,"Base de datos","database","");
        AddField(form,"Usuario de conexión SQL","user","");
        AddField(form,"Contraseña SQL","password",""); fields["password"].UseSystemPasswordChar=true;
        AddField(form,"Certificado CA (.pem/.crt)","ca","");
        var browse = new Button {Text="Elegir…",AutoSize=true}; form.Controls.Add(browse,2,5);
        browse.Click += delegate { using (var dialog = new OpenFileDialog {Filter="Certificado CA|*.pem;*.crt|Todos|*.*"}) if(dialog.ShowDialog()==DialogResult.OK) fields["ca"].Text=dialog.FileName; };
        AddField(form,"Usuario NovaPOS (nombre)","pos_user","");
        AddField(form,"Caja (nombre)","point","");
        foreach(var field in fields.Values) field.TextChanged += delegate {confirmed=null; checkedValues=null; activate.Enabled=false;};
        layout.Controls.Add(form);
        actions.Controls.AddRange(new Control[]{check,activate,open,stop}); layout.Controls.Add(actions); layout.Controls.Add(status);
        Log("Solo llena los datos para la PRIMERA vinculación. Si ya está activado, pulsa Abrir NovaPOS.\r\nEl usuario debe tener un turno abierto online en esa caja. Las contraseñas se guardan cifradas para este usuario Windows.\r\nSe necesita internet para vincular. Después se usa la misma interfaz y el cambio offline es automático para las operaciones admitidas.");
        check.Click += delegate { StartWorker("check",Values()); };
        activate.Click += delegate {
            if(confirmed==null || checkedValues==null) return;
            var message="Vincular exclusivamente a:\r\n" + confirmed["user_name"] + " · " + confirmed["point_name"] + "\r\nTurno " + confirmed["turn_id"] + "\r\n\r\nSe descargará una copia autorizada; no se modificarán datos de la nube. ¿Continuar?";
            if(MessageBox.Show(message,"Confirmar vinculación",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes) return;
            var values=new Dictionary<string,object>(checkedValues); values["confirmed"]=confirmed;
            StartWorker("activate", values);
        };
        open.Click += delegate { if(child!=null && !child.HasExited) OpenBrowser(); else StartWorker("serve",null); };
        stop.Click += delegate { StopWorker(); };
#endif
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if(child!=null && !child.HasExited) {e.Cancel=true; closeWhenStopped=true; StopWorker();}
            else if(busy) {e.Cancel=true; Log("Espera a que termine la instalación/vinculación para cerrar.");}
        };
    }
    void AddField(TableLayoutPanel panel,string label,string key,string value) {
        int row=panel.RowCount++; var input=new TextBox {Text=value,Dock=DockStyle.Fill,Margin=new Padding(8,4,4,4)};
        fields[key]=input; panel.Controls.Add(new Label {Text=label,AutoSize=true,Anchor=AnchorStyles.Left,Margin=new Padding(0,7,0,7)},0,row); panel.Controls.Add(input,1,row);
    }
    Dictionary<string,object> Values() {var result=new Dictionary<string,object>(); foreach(var pair in fields)result[pair.Key]=pair.Value.Text; return result;}
    void Log(string text) { if(IsDisposed) return; if(InvokeRequired) {BeginInvoke(new Action<string>(Log),text); return;} status.AppendText(text+Environment.NewLine+Environment.NewLine); }
    void OpenBrowser() {Process.Start(new ProcessStartInfo("http://127.0.0.1:8000/") {UseShellExecute=true});}
    void StartWorker(string action, Dictionary<string,object> values) {
        if(busy || (child!=null && !child.HasExited))return;
        busy=true; check.Enabled=false; activate.Enabled=false; open.Enabled=false;
        foreach(var field in fields.Values)field.Enabled=false;
        var start=new ProcessStartInfo(Path.Combine(root,"python","python.exe"),"-B \""+Path.Combine(root,"app","windows_installer","worker.py")+"\" "+action) {
            UseShellExecute=false, CreateNoWindow=true, WindowStyle=ProcessWindowStyle.Hidden, WorkingDirectory=Path.Combine(root,"app"),
            RedirectStandardInput=true, RedirectStandardOutput=true, RedirectStandardError=true, StandardOutputEncoding=Encoding.UTF8, StandardErrorEncoding=Encoding.UTF8
        };
        foreach(string key in new string[]{"PYTHONHOME","PYTHONPATH","DJANGO_SETTINGS_MODULE","NOVA_INTEGRATED_CONFIG","NOVA_LOCAL_CONFIG","RUN_MAIN","DATABASE_URL"})start.EnvironmentVariables.Remove(key);
        start.EnvironmentVariables["PYTHONUTF8"]="1";
        try {
            child=new Process {StartInfo=start, EnableRaisingEvents=true};
            child.OutputDataReceived += delegate(object sender,DataReceivedEventArgs e) {
                if(e.Data==null || !e.Data.StartsWith("{"))return;
                try {
                    var data=json.Deserialize<Dictionary<string,object>>(e.Data);
                    if(data.ContainsKey("message"))Log((string)data["message"]);
                    BeginInvoke(new Action(delegate {
                        if(data.ContainsKey("confirmed")) {confirmed=(Dictionary<string,object>)data["confirmed"]; checkedValues=values; Log("Usuario: "+confirmed["user_name"]+" · Caja: "+confirmed["point_name"]+" · Turno: "+confirmed["turn_id"]);}
                        if(data.ContainsKey("ready"))OpenBrowser();
                    }));
                } catch { /* Only the structured status protocol is shown, never SQL/tracebacks. */ }
            };
            child.ErrorDataReceived += delegate { /* Deliberately do not persist raw credential-bearing exceptions. */ };
            child.Exited += delegate {
                BeginInvoke(new Action(delegate {
                    busy=false; stop.Enabled=false; check.Enabled=true; open.Enabled=true;
                    foreach(var field in fields.Values)field.Enabled=true;
                    activate.Enabled=confirmed!=null;
                    if(child.ExitCode!=0)Log("El proceso no terminó correctamente. Conserva los datos. No desactives Smart App Control ni Defender.");
                    if(closeWhenStopped)Close();
                }));
            };
            child.Start(); child.BeginOutputReadLine(); child.BeginErrorReadLine();
            if(values!=null) {child.StandardInput.WriteLine(json.Serialize(values));child.StandardInput.Close();}
            stop.Enabled=action=="serve";
        } catch {busy=false;check.Enabled=true;open.Enabled=true; foreach(var field in fields.Values)field.Enabled=true; Log("Windows no pudo iniciar Python. Revisa el bloqueo o las dependencias; no desactives las protecciones.");}
    }
    void StopWorker() {
        if(child==null || child.HasExited)return;
        if(!stop.Enabled) {Log("La vinculación sigue en curso. Espera a que termine; no se interrumpirá la copia."); closeWhenStopped=false;return;}
        stop.Enabled=false; Log("Deteniendo la aplicación y PostgreSQL de forma segura…");
        try {child.StandardInput.WriteLine("STOP");child.StandardInput.Flush();}catch {Log("No se pudo enviar la señal de cierre. Revisa el proceso antes de apagar el equipo.");}
    }
#if PACKAGE
    static string InstallRoot() {return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Programs","NovaPOS");}
    static void Extract(string target) {
        target=Path.GetFullPath(target);
        if(Directory.Exists(target) || File.Exists(target))throw new IOException("Ya existe una instalación; no se reemplaza.");
        var drive=new DriveInfo(Path.GetPathRoot(target)); if(drive.AvailableFreeSpace < 3L*1024*1024*1024)throw new IOException("Falta espacio.");
        using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("NovaPayload")) {
            using(var sha=SHA256.Create()) {var hash=BitConverter.ToString(sha.ComputeHash(stream)).Replace("-","").ToLowerInvariant(); if(hash!=Payload.Sha256)throw new IOException("Paquete alterado.");}
            stream.Position=0;
            using(var archive=new ZipArchive(stream,ZipArchiveMode.Read)) {
                Directory.CreateDirectory(target); File.WriteAllText(Path.Combine(target,".installing"),"Instalación incompleta: no elimines carpetas con datos.");
                foreach(var item in archive.Entries) {
                    var path=Path.GetFullPath(Path.Combine(target,item.FullName.Replace('/',Path.DirectorySeparatorChar)));
                    if(!path.StartsWith(target+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase) || item.FullName.Contains(":"))throw new IOException("Ruta inválida.");
                    Directory.CreateDirectory(Path.GetDirectoryName(path));
                    using(var input=item.Open())using(var output=new FileStream(path,FileMode.CreateNew))input.CopyTo(output);
                }
                File.Delete(Path.Combine(target,".installing"));
            }
        }
    }
    static void Shortcuts(string target) {
        var type=Type.GetTypeFromProgID("WScript.Shell"); dynamic shell=Activator.CreateInstance(type);
        foreach(var folder in new string[]{Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),Environment.GetFolderPath(Environment.SpecialFolder.StartMenu)}) {
            dynamic link=shell.CreateShortcut(Path.Combine(folder,"NovaPOS.lnk")); link.TargetPath=Path.Combine(target,"NovaPOS.exe");link.WorkingDirectory=target;link.Description="NovaPOS online y offline";link.Save();
        }
    }
#endif
}
