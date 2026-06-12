project('rx_worker', 'c', version: '0.1', default_options: ['c_std=c11'])
dpdk = dependency('libdpdk', required: false)
if not dpdk.found()
  dpdk = dependency('dpdk')
endif
executable('rx_worker', 'rx_worker.c', dependencies: dpdk, install: true)
