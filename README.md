# SukiSU kernel build for Xiaomi Mi 8

This repository builds a minimal source-integrated SukiSU kernel for `dipper`
and packages it as an AnyKernel3 recovery ZIP.

The build is pinned to commit
`2e1cfd38e5f3b53351d3d59c797d14ff7f050611` from the public
`duckyduckG/android_kernel_xiaomi_sdm845_419` repository, matching the
2026-02-03 LineageOS 23.2 build. It integrates SukiSU Ultra `v3.2.0` with
manual hooks and KPM. SUSFS is intentionally omitted from this boot/root
validation build.

For Linux 4.19 SELinux policy structures, the build restores only
`kernel/selinux/sepolicy.c` from KernelSU `v0.9.5` commit `b766b985`. SukiSU
v3.2.0 removed those legacy compatibility branches while retaining the same
public policy helper interface.

Run **Actions > Build exact LineageOS SukiSU kernel for Xiaomi Mi 8 > Run
workflow**. Download the `SukiSU-dipper-Lineage23.2-exact-root-v3` artifact
after the job succeeds.

Do not use this artifact on any device other than Xiaomi Mi 8 (`dipper`) or on
a ROM that does not use the Xiaomi SDM845 4.19 kernel base.
