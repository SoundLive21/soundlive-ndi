/* SoundLive-NDI
 * Auteur : DUVERNOY Matthieu & ChatGPT
 * contact@soundlive.fr
 * Société SoundLive */
/* Lanceur natif Windows. Le code de la régie et ses données restent dans le
 * dossier qui contient cet exécutable. */
#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#include <windows.h>
#else
typedef uint16_t WCHAR;
typedef uint16_t WORD;
typedef uint32_t DWORD;
typedef int BOOL;
typedef void *HANDLE;
typedef void *HWND;
typedef struct {
    DWORD cb;
    WCHAR *lpReserved, *lpDesktop, *lpTitle;
    DWORD dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars;
    DWORD dwFillAttribute, dwFlags;
    WORD wShowWindow, cbReserved2;
    unsigned char *lpReserved2;
    HANDLE hStdInput, hStdOutput, hStdError;
} STARTUPINFOW;
typedef struct {
    HANDLE hProcess, hThread;
    DWORD dwProcessId, dwThreadId;
} PROCESS_INFORMATION;
extern DWORD GetModuleFileNameW(HANDLE, WCHAR *, DWORD);
extern DWORD GetFileAttributesW(const WCHAR *);
extern DWORD GetLastError(void);
extern BOOL CreateProcessW(const WCHAR *, WCHAR *, void *, void *, BOOL, DWORD,
                          void *, const WCHAR *, STARTUPINFOW *, PROCESS_INFORMATION *);
extern BOOL CloseHandle(HANDLE);
extern void ExitProcess(unsigned);
extern int MessageBoxW(HWND, const WCHAR *, const WCHAR *, unsigned);
#define CREATE_NO_WINDOW 0x08000000
#define INVALID_FILE_ATTRIBUTES ((DWORD)-1)
#define MB_ICONERROR 0x10
#endif

#define CAPACITY 32768

static const WCHAR title[] = L"SoundLive-NDI";
static WCHAR directory[CAPACITY];
static WCHAR executable_command[256];
static STARTUPINFOW startup;
static PROCESS_INFORMATION process;

static void copy_text(WCHAR *target, const WCHAR *source) {
    while ((*target++ = *source++)) {}
}

static void fail(const WCHAR *message) {
    MessageBoxW(NULL, message, title, MB_ICONERROR);
    ExitProcess(1);
}

static BOOL launch(const WCHAR *command) {
    copy_text(executable_command, command);
    if (!CreateProcessW(NULL, executable_command, NULL, NULL, 0, CREATE_NO_WINDOW,
                        NULL, directory, &startup, &process))
        return 0;
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 1;
}

void mainCRTStartup(void) {
    DWORD length = GetModuleFileNameW(NULL, directory, CAPACITY);
    if (length == 0 || length >= CAPACITY)
        fail(L"Impossible de retrouver le dossier de la régie.");
    while (length && directory[length - 1] != L'\\')
        length--;
    if (!length || length + 24 >= CAPACITY)
        fail(L"Impossible de retrouver le dossier de la régie.");
    copy_text(directory + length, L"regie-windows.py");
    if (GetFileAttributesW(directory) == INVALID_FILE_ATTRIBUTES)
        fail(L"Décompresse tout le ZIP. Place le lanceur à côté de regie-windows.py.");
    directory[length] = 0;
    startup.cb = sizeof(startup);
    if (launch(L"pyw.exe -3 -B regie-windows.py") ||
        launch(L"pythonw.exe -B regie-windows.py") ||
        launch(L"py.exe -3 -B regie-windows.py") ||
        launch(L"python.exe -B regie-windows.py"))
        ExitProcess(0);
    fail(L"Python 3 est introuvable. Installe Python pour Windows avec Tcl/Tk et le lanceur Python.");
}
