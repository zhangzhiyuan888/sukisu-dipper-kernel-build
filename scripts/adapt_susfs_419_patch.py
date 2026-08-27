#!/usr/bin/env python3

import argparse
import re
from pathlib import Path


SKIPPED_HUNKS = {
    "@@ -954,7 +1053,17 @@ vfs_kern_mount(struct file_system_type *type, int flags, const char *name, void\n",
    "@@ -1009,7 +1127,52 @@ static struct mount *clone_mnt(struct mount *old, struct dentry *root,\n",
}


def prepare_patch(source_path: Path, output_path: Path) -> None:
    lines = source_path.read_text().splitlines(keepends=True)
    output = []
    skipped = set()
    index = 0

    while index < len(lines):
        line = lines[index]
        if line in SKIPPED_HUNKS:
            skipped.add(line)
            index += 1
            while index < len(lines):
                if lines[index].startswith("@@ ") or lines[index].startswith("diff --git "):
                    break
                index += 1
            continue
        output.append(line)
        index += 1

    if skipped != SKIPPED_HUNKS:
        missing = sorted(header.strip() for header in SKIPPED_HUNKS - skipped)
        raise SystemExit(f"SUSFS patch hunk headers changed: {missing}")

    output_path.write_text("".join(output))


def replace_once(path: Path, anchor: str, replacement: str) -> None:
    source = path.read_text()
    count = source.count(anchor)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    path.write_text(source.replace(anchor, replacement, 1))


def patch_namespace(kernel_root: Path) -> None:
    path = kernel_root / "fs/namespace.c"

    anchor = '\tmnt = alloc_vfsmnt(fc->source ?: "none");\n'
    replacement = '''#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
\tif (unlikely(susfs_is_current_ksu_domain()))
\t\tmnt = alloc_vfsmnt(fc->source ?: "none", true, 0);
\telse
\t\tmnt = alloc_vfsmnt(fc->source ?: "none", false, 0);
#else
\tmnt = alloc_vfsmnt(fc->source ?: "none");
#endif
'''
    replace_once(path, anchor, replacement)

    anchor = '''\tstruct mount *mnt;
\tint err;

\tmnt = alloc_vfsmnt(old->mnt_devname);
'''
    replacement = '''\tstruct mount *mnt;
\tint err;

#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
\tbool is_current_ksu_domain = susfs_is_current_ksu_domain();
\tbool is_current_zygote_domain = susfs_is_current_zygote_domain();

\tif (unlikely(is_current_ksu_domain)) {
\t\tif (!(flag & CL_COPY_MNT_NS)) {
\t\t\tmnt = alloc_vfsmnt(old->mnt_devname, true, 0);
\t\t} else {
\t\t\tmnt = alloc_vfsmnt(old->mnt_devname, true, old->mnt_id);
\t\t\tif (mnt)
\t\t\t\tmnt->mnt.susfs_mnt_id_backup = DEFAULT_SUS_MNT_ID_FOR_KSU_PROC_UNSHARE;
\t\t}
\t} else if (likely(is_current_zygote_domain) &&
\t\t   old->mnt_id >= DEFAULT_SUS_MNT_ID) {
\t\tmnt = alloc_vfsmnt(old->mnt_devname, true, 0);
\t} else if ((flag & CL_COPY_MNT_NS) &&
\t\t   old->mnt_id >= DEFAULT_SUS_MNT_ID) {
\t\tmnt = alloc_vfsmnt(old->mnt_devname, true, 0);
\t} else {
\t\tmnt = alloc_vfsmnt(old->mnt_devname, false, 0);
\t}
#else
\tmnt = alloc_vfsmnt(old->mnt_devname);
#endif
'''
    replace_once(path, anchor, replacement)

    patch_susfs_header_419(kernel_root)
    patch_susfs_fsnotify_419(kernel_root)
    patch_sukisu_susfs_extra_work(kernel_root)
    upgrade_legacy_susfs_helpers(kernel_root)
    upgrade_legacy_mount_constants(kernel_root)
    upgrade_legacy_state_checks(kernel_root)


def patch_susfs_header_419(kernel_root: Path) -> None:
    path = kernel_root / "include/linux/susfs_def.h"
    anchor = "#include <linux/bits.h>\n"
    replace_once(path, anchor, anchor + "#include <linux/cred.h>\n")


def patch_susfs_fsnotify_419(kernel_root: Path) -> None:
    path = kernel_root / "fs/susfs.c"
    source = path.read_text()
    pattern = re.compile(
        r"static int susfs_handle_sdcard_inode_event\(struct fsnotify_mark \*mark, u32 mask,\n"
        r"\s+struct inode \*inode, struct inode \*dir,\n"
        r"\s+const struct qstr \*file_name, u32 cookie\)\n"
        r"\{\n"
        r"\tif \(!file_name \|\| file_name->len != 7 \|\|\n"
        r'\t    memcmp\(file_name->name, "Android", 7\)\)\n'
        r"\t\treturn 0;\n"
    )
    replacement = '''#if LINUX_VERSION_CODE < KERNEL_VERSION(5, 10, 0)
static int susfs_handle_sdcard_inode_event(struct fsnotify_group *group,
											struct inode *inode, u32 mask,
											const void *data, int data_type,
											const unsigned char *file_name, u32 cookie,
											struct fsnotify_iter_info *iter_info)
{
	if (!file_name || strcmp(file_name, "Android"))
		return 0;
#else
static int susfs_handle_sdcard_inode_event(struct fsnotify_mark *mark, u32 mask,
											struct inode *inode, struct inode *dir,
											const struct qstr *file_name, u32 cookie)
{
	if (!file_name || file_name->len != 7 ||
	    memcmp(file_name->name, "Android", 7))
		return 0;
#endif
'''
    source, count = pattern.subn(replacement, source)
    if count != 1:
        raise SystemExit(f"{path}: expected one fsnotify handler, found {count}")
    path.write_text(source)

    replace_once(
        path,
        "\t.handle_inode_event = susfs_handle_sdcard_inode_event,\n",
        '''#if LINUX_VERSION_CODE < KERNEL_VERSION(5, 10, 0)
	.handle_event = susfs_handle_sdcard_inode_event,
#else
	.handle_inode_event = susfs_handle_sdcard_inode_event,
#endif
''',
    )


def patch_sukisu_susfs_extra_work(kernel_root: Path) -> None:
    path = kernel_root / "KernelSU/kernel/hook/lsm_hook.c"
    anchor = '''extern struct work_struct susfs_extra_works;

static inline void ksu_handle_extra_susfs_work(void)
{
    if (work_pending(&susfs_extra_works))
        return;

    schedule_work(&susfs_extra_works);
}
'''
    replacement = '''#ifdef CONFIG_KSU_SUSFS_SUS_PATH
extern void susfs_run_sus_path_loop(void);
#endif

static inline void ksu_handle_extra_susfs_work(void)
{
    const struct cred *saved = override_creds(ksu_cred);

#ifdef CONFIG_KSU_SUSFS_SUS_PATH
    susfs_run_sus_path_loop();
#endif

    revert_creds(saved);
}
'''
    replace_once(path, anchor, replacement)


def upgrade_legacy_susfs_helpers(kernel_root: Path) -> None:
    path = kernel_root / "fs/namei.c"
    replace_once(
        path,
        "extern struct filename* susfs_get_redirected_path(unsigned long ino);\n",
        "extern struct filename *susfs_open_redirect_spoof_do_sys_openat(struct inode *inode);\n",
    )
    replace_once(
        path,
        "susfs_get_redirected_path(filp->f_inode->i_ino)",
        "susfs_open_redirect_spoof_do_sys_openat(filp->f_inode)",
    )

    path = kernel_root / "fs/proc/task_mmu.c"
    replace_once(
        path,
        "extern void susfs_sus_ino_for_show_map_vma(unsigned long ino, dev_t *out_dev, unsigned long *out_ino);\n",
        "extern void susfs_sus_kstat_spoof_show_map_vma(struct inode *inode, dev_t *out_dev, unsigned long *out_ino);\n",
    )
    replace_once(
        path,
        "susfs_sus_ino_for_show_map_vma(inode->i_ino, &dev, &ino);",
        "susfs_sus_kstat_spoof_show_map_vma((struct inode *)inode, &dev, &ino);",
    )

    path = kernel_root / "fs/stat.c"
    replace_once(
        path,
        "extern void susfs_sus_ino_for_generic_fillattr(unsigned long ino, struct kstat *stat);\n",
        "extern void susfs_sus_kstat_spoof_generic_fillattr(struct inode *inode, struct kstat *stat);\n",
    )
    replace_once(
        path,
        "susfs_sus_ino_for_generic_fillattr(inode->i_ino, stat);",
        "susfs_sus_kstat_spoof_generic_fillattr(inode, stat);",
    )

    upgrade_legacy_readdir_helper(kernel_root)


def upgrade_legacy_readdir_helper(kernel_root: Path) -> None:
    path = kernel_root / "fs/readdir.c"
    replace_once(
        path,
        "extern int susfs_sus_ino_for_filldir64(unsigned long ino);\n",
        '''extern bool susfs_is_inode_sus_path(struct inode *inode);

static bool susfs_should_hide_inode(struct super_block *sb, unsigned long ino)
{
	struct inode *inode = ilookup(sb, ino);
	bool hide = false;

	if (inode) {
		hide = susfs_is_inode_sus_path(inode);
		iput(inode);
	}
	return hide;
}
''',
    )

    for struct_name in (
        "readdir_callback",
        "getdents_callback",
        "getdents_callback64",
        "compat_readdir_callback",
        "compat_getdents_callback",
    ):
        anchor = f"struct {struct_name} {{\n\tstruct dir_context ctx;\n"
        replacement = anchor + '''#ifdef CONFIG_KSU_SUSFS_SUS_PATH
	struct super_block *susfs_sb;
#endif
'''
        replace_once(path, anchor, replacement)

    source = path.read_text()
    old_call = "susfs_sus_ino_for_filldir64(ino)"
    if source.count(old_call) != 4:
        raise SystemExit(
            f"{path}: expected four legacy readdir calls, "
            f"found {source.count(old_call)}"
        )
    source = source.replace(
        old_call,
        "susfs_should_hide_inode(buf->susfs_sb, ino)",
    )

    anchor = "\terror = iterate_dir(f.file, &buf.ctx);\n"
    if source.count(anchor) != 5:
        raise SystemExit(
            f"{path}: expected five iterate_dir calls, found {source.count(anchor)}"
        )
    replacement = '''#ifdef CONFIG_KSU_SUSFS_SUS_PATH
	buf.susfs_sb = file_inode(f.file)->i_sb;
#endif
	error = iterate_dir(f.file, &buf.ctx);
'''
    path.write_text(source.replace(anchor, replacement))


def upgrade_legacy_mount_constants(kernel_root: Path) -> None:
    files = [
        "fs/namespace.c",
        "fs/proc/fd.c",
        "fs/proc_namespace.c",
        "fs/statfs.c",
    ]
    replacements = {
        "DEFAULT_SUS_MNT_ID": "DEFAULT_KSU_MNT_ID",
        "DEFAULT_SUS_MNT_GROUP_ID": "DEFAULT_KSU_MNT_GROUP_ID",
        "DEFAULT_SUS_MNT_ID_FOR_KSU_PROC_UNSHARE": "SUSFS_419_KSU_UNSHARE_MNT_ID",
    }

    path = kernel_root / "fs/namespace.c"
    anchor = "#define CL_COPY_MNT_NS BIT(25) /* used by copy_mnt_ns() */\n"
    replacement = anchor + "#define SUSFS_419_KSU_UNSHARE_MNT_ID 1000000\n"
    replace_once(path, anchor, replacement)

    for relative_path in files:
        path = kernel_root / relative_path
        source = path.read_text()
        for old_name, new_name in replacements.items():
            source = re.sub(rf"\b{old_name}\b", new_name, source)
        path.write_text(source)

    for relative_path in files:
        source = (kernel_root / relative_path).read_text()
        for legacy_name in replacements:
            if re.search(rf"\b{legacy_name}\b", source):
                raise SystemExit(
                    f"{relative_path}: legacy SUSFS mount constant remains: "
                    f"{legacy_name}"
                )


def upgrade_legacy_state_checks(kernel_root: Path) -> None:
    files = [
        "fs/dcache.c",
        "fs/namei.c",
        "fs/notify/fdinfo.c",
        "fs/proc/fd.c",
        "fs/proc/task_mmu.c",
        "fs/readdir.c",
        "fs/stat.c",
        "fs/statfs.c",
    ]
    inode_flags = {
        "INODE_STATE_SUS_PATH": "AS_FLAGS_SUS_PATH",
        "INODE_STATE_SUS_KSTAT": "AS_FLAGS_SUS_KSTAT",
        "INODE_STATE_OPEN_REDIRECT": "AS_FLAGS_OPEN_REDIRECT",
    }
    expression = r"[A-Za-z_][A-Za-z0-9_]*(?:->[A-Za-z_][A-Za-z0-9_]*)*"

    for relative_path in files:
        path = kernel_root / relative_path
        source = path.read_text()
        source = source.replace(
            "current->susfs_task_state & TASK_STRUCT_NON_ROOT_USER_APP_PROC",
            "susfs_is_current_proc_umounted_app()",
        )
        for old_flag, new_flag in inode_flags.items():
            source = re.sub(
                rf"({expression})->i_state & {old_flag}",
                rf"test_bit({new_flag}, &\1->i_mapping->flags)",
                source,
            )
        source = re.sub(
            rf"({expression})->i_state & INODE_STATE_SUS_MOUNT",
            "false",
            source,
        )
        if relative_path == "fs/statfs.c":
            source = source.replace(
                "susfs_is_current_proc_umounted_app()",
                "susfs_is_current_proc_umounted()",
            )
        path.write_text(source)

    for relative_path in files:
        source = (kernel_root / relative_path).read_text()
        for legacy_name in (
            "TASK_STRUCT_NON_ROOT_USER_APP_PROC",
            "INODE_STATE_SUS_PATH",
            "INODE_STATE_SUS_KSTAT",
            "INODE_STATE_OPEN_REDIRECT",
            "INODE_STATE_SUS_MOUNT",
        ):
            if legacy_name in source:
                raise SystemExit(
                    f"{relative_path}: legacy SUSFS state remains: {legacy_name}"
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("output", type=Path)

    namespace = subparsers.add_parser("patch-namespace")
    namespace.add_argument("kernel_root", type=Path)

    args = parser.parse_args()
    if args.command == "prepare":
        prepare_patch(args.source, args.output)
    else:
        patch_namespace(args.kernel_root)


if __name__ == "__main__":
    main()
