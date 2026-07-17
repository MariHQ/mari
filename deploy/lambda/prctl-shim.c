#define _GNU_SOURCE

#include <dlfcn.h>
#include <errno.h>
#include <stdarg.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

typedef int (*prctl_fn)(int, unsigned long, unsigned long, unsigned long, unsigned long);
typedef int (*posix_fallocate_fn)(int, off_t, off_t);

int prctl(int option, ...)
{
    va_list args;
    unsigned long arg2;
    unsigned long arg3;
    unsigned long arg4;
    unsigned long arg5;
    static prctl_fn real_prctl;

    if (option == PR_SET_PDEATHSIG)
        return 0;

    va_start(args, option);
    arg2 = va_arg(args, unsigned long);
    arg3 = va_arg(args, unsigned long);
    arg4 = va_arg(args, unsigned long);
    arg5 = va_arg(args, unsigned long);
    va_end(args);

    if (!real_prctl)
        real_prctl = (prctl_fn)dlsym(RTLD_NEXT, "prctl");
    if (!real_prctl) {
        errno = ENOSYS;
        return -1;
    }
    return real_prctl(option, arg2, arg3, arg4, arg5);
}

/*
 * Lambda's seccomp policy rejects fallocate(2) with EPERM. PostgreSQL uses
 * posix_fallocate while extending relations and treats that result as fatal.
 * Preserve the normal implementation everywhere else, but emulate allocation
 * with ftruncate when the Lambda runtime blocks the syscall. PostgreSQL writes
 * each page immediately afterward, so preallocation is not required here.
 */
int posix_fallocate(int fd, off_t offset, off_t length)
{
    static posix_fallocate_fn real_posix_fallocate;
    int result;
    struct stat st;
    off_t end;

    if (!real_posix_fallocate)
        real_posix_fallocate = (posix_fallocate_fn)dlsym(RTLD_NEXT, "posix_fallocate");

    if (real_posix_fallocate) {
        result = real_posix_fallocate(fd, offset, length);
        if (result != EPERM && result != ENOSYS && result != EOPNOTSUPP)
            return result;
    }

    if (offset < 0 || length < 0 || __builtin_add_overflow(offset, length, &end))
        return EINVAL;
    if (fstat(fd, &st) != 0)
        return errno;
    if (st.st_size < end && ftruncate(fd, end) != 0)
        return errno;
    return 0;
}
