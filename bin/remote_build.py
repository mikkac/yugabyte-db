#!/usr/bin/env python3

import argparse
import os
import shlex
import subprocess
import sys


def check_output(args):
    bytes = subprocess.check_output(args)
    return bytes.decode('utf-8')


def check_output_line(args):
    return check_output(args).strip()


def check_output_lines(args):
    return [file.strip() for file in check_output(args).split('\n')]


def parse_name_status(lines):
    files = []
    for line in lines:
        tokens = [x.strip() for x in line.split('\t')]
        if len(tokens) == 0 or tokens[0] == 'D' or tokens[0] == '':
            continue
        if tokens[0].startswith('R'):
            name = tokens[2]
        else:
            name = tokens[1]
        files.append(name)
    return files


def remote_communicate(args, remote_command):
    args = ['ssh', args.host, remote_command]
    proc = subprocess.Popen(args, shell=False)
    proc.communicate()
    if proc.returncode != 0:
        sys.exit(proc.returncode)


def check_remote_files(args, files):
    remote_command = "cd {0} && git diff --name-status".format(args.remote_path)
    remote_changed = parse_name_status(check_output_lines(['ssh', args.host, remote_command]))
    unexpected = []
    for changed in remote_changed:
        if changed not in files:
            unexpected.append(changed)
    if unexpected:
        command = 'cd {0}'.format(args.remote_path)
        message = 'Reverting:\n'
        for file in unexpected:
            message += '  {0}\n'.format(file)
            command += ' && git checkout -- {0}'.format(shlex.quote(file))
        print(message)
        remote_communicate(args, command)


def remote_output_line(args, command):
    return check_output_line(['ssh', args.host, 'cd {0} && {1}'.format(args.remote_path, command)])


def fetch_remote_commit(args):
    return remote_output_line(args, 'git rev-parse HEAD')


def main():
    parser = argparse.ArgumentParser(prog=sys.argv[0])
    parser.add_argument('--host', type=str, default=None, help='host for build')
    home = os.path.expanduser('~')
    cwd = os.getcwd()
    default_path = '~/{0}'.format(cwd[len(home) + 1:] if cwd.startswith(home) else 'code/yugabyte')
    parser.add_argument('--remote-path',
                        type=str,
                        default=default_path,
                        help='path used for build')
    parser.add_argument('--branch', type=str, default='origin/master', help='base branch for build')
    parser.add_argument('--build-type', type=str, default='debug', help='build type')
    parser.add_argument('--skip-build',
                        action='store_const',
                        const=True,
                        default=False,
                        help='skip build, only sync files')
    parser.add_argument('args', nargs=argparse.REMAINDER, help='arguments for yb_build.sh')

    args = parser.parse_args()

    if args.host is None and "YB_REMOTE_BUILD_HOST" in os.environ:
        args.host = os.environ["YB_REMOTE_BUILD_HOST"]

    if args.host is None:
        sys.stderr.write(
            "Please specify host with --host option or YB_REMOTE_BUILD_HOST variable\n")
        sys.exit(1)

    print("Host: {0}, build type: {1}, remote path: {2}".format(args.host,
                                                                args.build_type,
                                                                args.remote_path))

    commit = check_output_line(['git', 'merge-base', args.branch, 'HEAD'])
    print("Base commit: {0}".format(commit))

    remote_commit = fetch_remote_commit(args)
    if remote_commit != commit:
        print("Remote commit mismatch, syncing")
        remote_command = 'cd {0} && '.format(args.remote_path)
        remote_command += 'git checkout -- . && '
        remote_command += 'git clean -f . && '
        remote_command += 'git checkout master && '
        remote_command += 'git pull && '
        remote_command += 'git checkout {0}'.format(commit)
        remote_communicate(args, remote_command)
        remote_commit = fetch_remote_commit(args)
        if remote_commit != commit:
            sys.stderr.write("Failed to sync remote commit to: {0}, it still: {1}".format(
                commit, remote_commit))
            sys.exit(1)

    files = parse_name_status(check_output_lines(['git', 'diff', commit, '--name-status']))
    print("Total files: {0}".format(len(files)))

    if files:
        # From this StackOverflow thread: https://goo.gl/xzhBUC
        # The -a option is equivalent to -rlptgoD. You need to remove the -t. -t tells rsync to
        # transfer modification times along with the files and update them on the remote system.
        #
        # Another relevant one -- how to make rsync preserve timestamps of unchanged files:
        # https://goo.gl/czD96F
        #
        # We are using "rlpcgoD" instead of "rlptgoD" (with "t" replaced with "c").
        # The goal is to use checksums for deciding what files to skip.
        rsync_args = ['rsync', '-rlpcgoDvR']
        rsync_args += files
        rsync_args += ["{0}:{1}".format(args.host, args.remote_path)]
        proc = subprocess.Popen(rsync_args, shell=False)
        proc.communicate()
        if proc.returncode != 0:
            sys.exit(proc.returncode)

    check_remote_files(args, files)

    if args.skip_build:
        sys.exit(0)

    ybd_args = [args.build_type]
    if len(args.args) != 0 and args.args[0] == '--':
        ybd_args += args.args[1:]
    else:
        ybd_args += args.args
    remote_command = "cd {0} && ./yb_build.sh".format(args.remote_path)
    for arg in ybd_args:
        remote_command += " {0}".format(shlex.quote(arg))
    print("Remote command: {0}".format(remote_command))
    ssh_args = ['ssh', args.host, remote_command]
    proc = subprocess.Popen(ssh_args, shell=False)
    proc.communicate()
    if proc.returncode != 0:
        sys.exit(proc.returncode)


if __name__ == '__main__':
    main()
