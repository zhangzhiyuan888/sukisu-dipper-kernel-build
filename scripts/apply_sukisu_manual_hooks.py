#!/usr/bin/env python3

from pathlib import Path


GUARD = "defined(CONFIG_KSU) && defined(CONFIG_KSU_SUSFS)"


def replace_once(path: str, anchor: str, replacement: str) -> None:
    source_path = Path(path)
    source = source_path.read_text()
    count = source.count(anchor)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    source_path.write_text(source.replace(anchor, replacement, 1))


def replace_all(path: str, anchor: str, replacement: str) -> None:
    source_path = Path(path)
    source = source_path.read_text()
    count = source.count(anchor)
    if count < 1:
        raise SystemExit(f"{path}: expected at least one anchor")
    source_path.write_text(source.replace(anchor, replacement))


replace_once(
    "fs/exec.c",
    "static int do_execveat_common(int fd, struct filename *filename,\n",
    f"""#if {GUARD}
extern int ksu_handle_execveat(int *fd, struct filename **filename_ptr,
                void *argv, void *envp, int *flags);
#endif

static int do_execveat_common(int fd, struct filename *filename,
""",
)
replace_once(
    "fs/exec.c",
    """{
	return __do_execve_file(fd, filename, argv, envp, flags, NULL);
}
""",
    f"""{{
#if {GUARD}
	ksu_handle_execveat(&fd, &filename, &argv, &envp, &flags);
#endif
	return __do_execve_file(fd, filename, argv, envp, flags, NULL);
}}
""",
)

replace_once(
    "fs/open.c",
    "/*\n * access() needs to use the real uid/gid, not the effective uid/gid.\n",
    f"""#if {GUARD}
extern int ksu_handle_faccessat(int *dfd,
                const char __user **filename_user, int *mode, int *flags);
#endif

/*
 * access() needs to use the real uid/gid, not the effective uid/gid.
""",
)
replace_once(
    "fs/open.c",
    "\tunsigned int lookup_flags = LOOKUP_FOLLOW;\n\n\tif (mode & ~S_IRWXO)",
    f"""\tunsigned int lookup_flags = LOOKUP_FOLLOW;

#if {GUARD}
	ksu_handle_faccessat(&dfd, &filename, &mode, NULL);
#endif

	if (mode & ~S_IRWXO)""",
)

replace_once(
    "fs/stat.c",
    "/**\n * vfs_statx - Get basic and extra attributes by filename\n",
    f"""#if {GUARD}
extern int ksu_handle_stat(int *dfd,
                const char __user **filename_user, int *flags);
#endif

/**
 * vfs_statx - Get basic and extra attributes by filename
""",
)
replace_once(
    "fs/stat.c",
    "\tunsigned int lookup_flags = LOOKUP_FOLLOW | LOOKUP_AUTOMOUNT;\n\n\tif ((flags &",
    f"""\tunsigned int lookup_flags = LOOKUP_FOLLOW | LOOKUP_AUTOMOUNT;

#if {GUARD}
	ksu_handle_stat(&dfd, &filename, &flags);
#endif

	if ((flags &""",
)

for call in (
    "ksu_selinux_hide_handle_post_fs_data();",
    "ksu_selinux_hide_handle_second_stage();",
):
    replace_all(
        "KernelSU/kernel/runtime/ksud.c",
        f"    {call}\n",
        f"""#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0)
    {call}
#endif
""",
    )

checks = {
    "fs/exec.c": ["ksu_handle_execveat(&fd"],
    "fs/open.c": ["ksu_handle_faccessat(&dfd"],
    "fs/stat.c": ["ksu_handle_stat(&dfd"],
}
for path, needles in checks.items():
    source = Path(path).read_text()
    for needle in needles:
        if source.count(needle) != 1:
            raise SystemExit(f"{path}: hook verification failed for {needle}")

print("Applied and verified SukiSU manual hooks")
