project('tx_worker', 'c', version: '0.2', default_options: ['c_std=c11'])
dpdk = dependency('libdpdk', required: false)
if not dpdk.found()
  dpdk = dependency('dpdk')
endif
executable('tx_worker', 'tx_worker.c', dependencies: dpdk, install: true)
