import bitarray
from collections import deque
import datetime
import random
import os
import Queue

import bidict

import config
import ftlbuilder
import lrulist
import recorder

"""
This refactors Dftl

Notes for DFTL design

Components
- block pool: it should have free list, used data blocks and used translation
  blocks. We should be able to find out the next free block from here. The DFTL
  paper does not mention a in-RAM data structure like this. How do they find
  out the next free block?
    - it manages the appending point for different purpose. No, it does not
      have enough info to do that.

- Cached Mapping Table (CMT): this class should do the following:
    - have logical page <-> physical page mapping entries.
    - be able to translation LPN to PPN and PPN to LPN
    - implement a replacement policy
    - has a size() method to output the size of the CMT, so we know it does not
      exceed the size of SRAM
    - be able to add one entry
    - be able to remove one entry
    - be able to find the proper entry to be moved.
    - be able to set the number of entries allows by size
    - be able to set the number of entries directly
    - be able to fetch a mapping entry (this need to consult the Global
      Translation Directory)
    - be able to evict one entrie to flash
    - be able to evict entries to flash in batch

    - CMT interacts with
        - Flash: to save and read translation pages
        - block pool: to find free block to evict translation pages
        - Global Translation Directory: to find out where the translation pages
          are (so you can read/write), also, you need to update GTD when
          evicting pages to flash.
        - bitmap???

- Global mapping table (GMT): this class holds the mappings of all the data
  pages. In real implementation, this should be stored in flash. This data
  structure will be used intensively. It should have a good interface.
    - API, get_entries_of_Mvpn(virtual mapping page number). This is one of the
      cases that it may be used: when reading a data page, its translation
      entry is not in CMT. The translator will consult the GTD to find the
      physical translation page number of virtual translation page number V.
      Then we need to get the content of V by reading its corresponding
      physical page. We may provide an interface to the translator like
      load_translation_physical_page(PPN), which internally, we read from GMT.

- Out of Band Area (OOB) of a page: it can hold:
    - page state (Invalid, Valid, Erased)
    - logical page number
    - ECC

- Global Translation Directory, this should do the following:
    - maintains the locations of translation pages
    - given a Virtual Translation Page Number, find out the physical
      translation page number
    - given a Logical Data Page Number, find out the physical data page number

    - GTD should be a pretty passive class, it interacts with
        - CMT. When CMT changes the location of translation pages, GTD should
          be updated to reflect the changes

- Garbage Collector
    - clean data blocks: to not interrupt current writing of pages, garbage
      collector should have its own appending point to write the garbage
      collected data
    - clean translation blocks. This cleaning should also have its own
      appending point.
    - NOTE: the DFTL paper says DFTL also have partial merge and switch merge,
      I need to read their code to find out why.
    - NOTE2: When cleaning a victim block, we need to know the corresponding
      logical page number of a vadlid physical page in the block. However, the
      Global Mapping Table does not provide physical to logical mapping. We can
      maintain such an mapping table by our own and assume that information is
      stored in OOB.

    - How the garbage collector should interact with other components?
        - this cleaner will use free blocks and make used block free, so it
          will need to interact with block pool to move blocks between
          different lists.
        - the cleaner also need to interact with CMT because it may move
          translation pages around
        - the cleaner also need to interact with bitmap because it needs to
          find out the if a page is valid or not.  It also need to find out the
          invalid ratio of the blocks
        - the cleaner also need to update the global translation directory
          since it moved pages.
        - NOTE: all the classes above should a provide an easy interface for
          the cleaner to use, so the cleaner does not need to use the low-level
          interfaces to implement these functions

- Appending points: there should be several appending points:
    - appending point for writing translation page
    - appending point for writing data page
    - appending ponit for garbage collection (NO, the paper says there is no
      such a appending point
    - NOTE: these points should be maintained by block pool.

- FLASH



********* Victim block selection ****************
Pick the block with the largest benefit/cost

benefit/cost = age * (1-u) / 2u

where u is the utilization of the segment and age is the
time since the most recent modification (i.e., the last
block invalidation). The terms 2u and 1-u respectively
represent the cost for copying (u to read valid blocks in
the segment and u to write back them) and the free
space reclaimed.

What's age?
The longer the age, the more benefit it has.
Since ?
    - the time the block was erased
    - the first time a page become valid
    - the last time a page become valid
        - if use this and age is long, .... it does not say anything about
        overwriting
    - the last time a page become invalid in the block
        - if use this and age is long, the rest of the valid pages are cold
        (long time no overwrite)


********* Profiling result ****************
   119377    0.628    0.000    0.829    0.000 ftlbuilder.py:100(validate_page)
    93220    0.427    0.000    0.590    0.000 ftlbuilder.py:104(invalidate_page)
  6507243   94.481    0.000  266.729    0.000 ftlbuilder.py:131(block_valid_ratio)
 26133103  105.411    0.000  147.733    0.000 ftlbuilder.py:141(is_page_valid)
    10963    0.073    0.000    0.084    0.000 ftlbuilder.py:145(is_page_invalid)
        1    0.000    0.000    0.000    0.000 ftlbuilder.py:187(__init__)
        2    0.000    0.000    0.000    0.000 ftlbuilder.py:75(__init__)
 26356663   42.697    0.000   42.697    0.000 ftlbuilder.py:86(pagenum_to_slice_range)
  6507243   29.636    0.000  296.394    0.000 dftl.py:1072(benefit_cost)
    24285   16.029    0.001  314.736    0.013 dftl.py:1125(victim_blocks_iter)
  6560378   12.825    0.000   12.825    0.000 config.py:53(block_to_page_range)


************** SRAM size ******************
In the DFTL paper, they say the minimum SRAM size is the size that is
required for hybrid FTL to work. In hybrid ftl, they use 3% of the flash
as log blocks. That means we need to keep the mapping for these 3% in SRAM.
For a 256MB flash, the number of pages we need to keep mapping for is
> (256*2^20/4096)*0.03
[1] 1966.08
about 2000 pages


*************** Batch update ***************
When evicting one mapping, do the following
- find all dirty mappings in the same translation page
- write all dirty mappings in the same translation page to flash
- mark all dirty mappings as clean
- delete the one mapping

"""

UNINITIATED, MISS = ('UNINIT', 'MISS')
DATA_BLOCK, TRANS_BLOCK = ('data_block', 'trans_block')
random.seed(0)

# debugrec = recorder.Recorder(recorder.STDOUT_TARGET, verbose_level = 3)
# def db(*args):
    # debugrec.debug(*args)

class OutOfBandAreas(object):
    """
    It is used to hold page state and logical page number of a page.
    It is not necessary to implement it as list. But the interface should
    appear to be so.  It consists of page state (bitmap) and logical page
    number (dict).  Let's proivde more intuitive interfaces: OOB should accept
    events, and react accordingly to this event. The action may involve state
    and lpn_of_phy_page.
    """
    def __init__(self, confobj):
        self.conf = confobj

        self.flash_num_blocks = confobj['flash_num_blocks']
        self.flash_npage_per_block = confobj['flash_npage_per_block']
        self.total_pages = self.flash_num_blocks * self.flash_npage_per_block

        # Key data structures
        self.states = ftlbuilder.FlashBitmap2(confobj)
        # ppn->lpn mapping stored in OOB
        self.ppn_to_lpn = {}

        # flash block -> last invalidation time
        # int -> timedate.timedate
        self.last_inv_time_of_block = {}

    def translate_ppn_to_lpn(self, ppn):
        return self.ppn_to_lpn[ppn]

    def wipe_ppn(self, ppn):
        self.states.invalidate_page(ppn)
        block, _ = self.conf.page_to_block_off(ppn)
        self.last_inv_time_of_block[block] = datetime.datetime.now()

        # It is OK to delay it until we erase the block
        # try:
            # del self.ppn_to_lpn[ppn]
        # except KeyError:
            # # it is OK that the key does not exist, for example,
            # # when discarding without writing to it
            # pass

    def erase_block(self, flash_block):
        self.states.erase_block(flash_block)

        start, end = self.conf.block_to_page_range(flash_block)
        for ppn in range(start, end):
            try:
                del self.ppn_to_lpn[ppn]
            except KeyError:
                pass

        del self.last_inv_time_of_block[flash_block]

    def new_write(self, lpn, old_ppn, new_ppn):
        """
        mark the new_ppn as valid
        update the LPN in new page's OOB to lpn
        invalidate the old_ppn, go cleaner can GC it
        """
        self.states.validate_page(new_ppn)
        self.ppn_to_lpn[new_ppn] = lpn

        if old_ppn != UNINITIATED:
            # the lpn has mapping before this write
            self.wipe_ppn(old_ppn)

    def lpns_of_block(self, flash_block):
        s, e = self.conf.block_to_page_range(flash_block)
        lpns = []
        for ppn in range(s, e):
            lpns.append(self.ppn_to_lpn.get(ppn, 'NA'))

        return lpns

class BlockPool(object):
    def __init__(self, confobj):
        self.conf = confobj

        self.freeblocks = deque(range(self.conf['flash_num_blocks']))

        # initialize usedblocks
        self.trans_usedblocks = []
        self.data_usedblocks  = []

    def pop_a_free_block(self):
        if self.freeblocks:
            blocknum = self.freeblocks.popleft()
        else:
            # nobody has free block
            raise RuntimeError('No free blocks in device!!!!')

        return blocknum

    def pop_a_free_block_to_trans(self):
        "take one block from freelist and add it to translation block list"
        blocknum = self.pop_a_free_block()
        self.trans_usedblocks.append(blocknum)
        return blocknum

    def pop_a_free_block_to_data(self):
        "take one block from freelist and add it to data block list"
        blocknum = self.pop_a_free_block()
        self.data_usedblocks.append(blocknum)
        return blocknum

    def move_used_data_block_to_free(self, blocknum):
        self.data_usedblocks.remove(blocknum)
        self.freeblocks.append(blocknum)

    def move_used_trans_block_to_free(self, blocknum):
        self.trans_usedblocks.remove(blocknum)
        self.freeblocks.append(blocknum)

    def total_used_blocks(self):
        return len(self.trans_usedblocks) + len(self.data_usedblocks)

    def used_blocks(self):
        return self.trans_usedblocks + self.data_usedblocks

    def next_page_to_program(self, log_end_name_str, pop_free_block_func):
        """
        The following comment uses next_data_page_to_program() as a example.

        it finds out the next available page to program
        usually it is the page after log_end_pagenum.

        If next=log_end_pagenum + 1 is in the same block with
        log_end_pagenum, simply return log_end_pagenum + 1
        If next=log_end_pagenum + 1 is out of the block of
        log_end_pagenum, we need to pick a new block from self.freeblocks

        This function is stateful, every time you call it, it will advance by
        one.
        """

        if not hasattr(self, log_end_name_str):
           # This is only executed for the first time
           cur_block = pop_free_block_func()
           # use the first page of this block to be the
           next_page = self.conf.block_off_to_page(cur_block, 0)
           # log_end_name_str is the page that is currently being operated on
           setattr(self, log_end_name_str, next_page)

           return next_page

        cur_page = getattr(self, log_end_name_str)
        cur_block, cur_off = self.conf.page_to_block_off(cur_page)

        next_page = (cur_page + 1) % self.conf.total_num_pages()
        next_block, next_off = self.conf.page_to_block_off(next_page)

        if cur_block == next_block:
            ret = next_page
        else:
            # get a new block
            block = pop_free_block_func()
            start, _ = self.conf.block_to_page_range(block)
            ret = start

        setattr(self, log_end_name_str, ret)
        return ret

    def next_data_page_to_program(self):
        return self.next_page_to_program('data_log_end_ppn',
            self.pop_a_free_block_to_data)

    def next_translation_page_to_program(self):
        return self.next_page_to_program('trans_log_end_ppn',
            self.pop_a_free_block_to_trans)

    def next_gc_data_page_to_program(self):
        return self.next_page_to_program('gc_data_log_end_ppn',
            self.pop_a_free_block_to_data)

    def next_gc_translation_page_to_program(self):
        return self.next_page_to_program('gc_trans_log_end_ppn',
            self.pop_a_free_block_to_trans)

    def current_blocks(self):
        try:
            cur_data_block, _ = self.conf.page_to_block_off(
                self.data_log_end_ppn)
        except AttributeError:
            cur_data_block = None

        try:
            cur_trans_block, _ = self.conf.page_to_block_off(
                self.trans_log_end_ppn)
        except AttributeError:
            cur_trans_block = None

        try:
            cur_gc_data_block, _ = self.conf.page_to_block_off(
                self.gc_data_log_end_ppn)
        except AttributeError:
            cur_gc_data_block = None

        try:
            cur_gc_trans_block, _ = self.conf.page_to_block_off(
                self.gc_trans_log_end_ppn)
        except AttributeError:
            cur_gc_trans_block = None

        return (cur_data_block, cur_trans_block, cur_gc_data_block,
            cur_gc_trans_block)

    def __repr__(self):
        ret = ' '.join(['freeblocks', repr(self.freeblocks)]) + '\n' + \
            ' '.join(['trans_usedblocks', repr(self.trans_usedblocks)]) + \
            '\n' + \
            ' '.join(['data_usedblocks', repr(self.data_usedblocks)])
        return ret

    def visual(self):
        block_states = [ 'O' if block in self.freeblocks else 'X'
                for block in range(self.conf['flash_num_blocks'])]
        return ''.join(block_states)


class CacheEntryData(object):
    """
    This is a helper class that store entry data for a LPN
    """
    def __init__(self, lpn, ppn, dirty):
        self.lpn = lpn
        self.ppn = ppn
        self.dirty = dirty

    def __repr__(self):
        return "lpn:{}, ppn:{}, dirty:{}".format(self.lpn,
            self.ppn, self.dirty)


class CachedMappingTable(object):
    """
    When do we need batched update?
    - do we need it when cleaning translation pages? NO. cleaning translation
    pages does not change contents of translation page.
    - do we need it when cleaning data page? Yes. When cleaning data page, you
    need to modify some lpn->ppn. For those LPNs in the same translation page,
    you can group them and update together. The process is: put those LPNs to
    the same group, read the translation page, modify entries and write it to
    flash. If you want batch updates here, you will need to buffer a few
    lpn->ppn. Well, since we have limited SRAM, you cannot do this.
    TODO: maybe you need to implement this.

    - do we need it when writing a lpn? To be exact, we need it when evict an
    entry in CMT. In that case, we need to find all the CMT entries in the same
    translation page with the victim entry.
    """
    def __init__(self, confobj):
        self.conf = confobj

        self.entry_bytes = 8 # lpn + ppn
        max_bytes = self.conf['dftl']['max_cmt_bytes']
        self.max_n_entries = (max_bytes + self.entry_bytes - 1) / \
            self.entry_bytes
        print 'cache max entries', self.max_n_entries

        # self.entries = {}
        # self.entries = lrulist.LruCache()
        self.entries = lrulist.SegmentedLruCache(self.max_n_entries, 0.5)

    def lpn_to_ppn(self, lpn):
        "Try to find ppn of the given lpn in cache"
        entry_data = self.entries.get(lpn, MISS)
        if entry_data == MISS:
            return MISS
        else:
            return entry_data.ppn

    def add_new_entry(self, lpn, ppn, dirty):
        "dirty is a boolean"
        if self.entries.has_key(lpn):
            raise RuntimeError("{}:{} already exists in CMT entries.".format(
                lpn, self.entries[lpn].ppn))
        self.entries[lpn] = CacheEntryData(lpn = lpn, ppn = ppn, dirty = dirty)

    def update_entry(self, lpn, ppn, dirty):
        "You may end up remove the old one"
        self.entries[lpn] = CacheEntryData(lpn = lpn, ppn = ppn, dirty = dirty)

    def overwrite_entry(self, lpn, ppn, dirty):
        "lpn must exist"
        self.entries[lpn].ppn = ppn
        self.entries[lpn].dirty = dirty

    def remove_entry_by_lpn(self, lpn):
        del self.entries[lpn]

    def victim_entry(self):
        # lpn = random.choice(self.entries.keys())
        classname = type(self.entries).__name__
        if classname in ('SegmentedLruCache', 'LruCache'):
            lpn = self.entries.victim_key()
        else:
            raise RuntimeError("You need to specify victim selection")

        # lpn, Cacheentrydata
        return lpn, self.entries.peek(lpn)

    def is_full(self):
        n = len(self.entries)
        assert n <= self.max_n_entries
        return n == self.max_n_entries

    def __repr__(self):
        return repr(self.entries)


class GlobalMappingTable(object):
    """
    This mapping table is for data pages, not for translation pages.
    GMT should have entries as many as the number of pages in flash
    """
    def __init__(self, confobj, flashobj):
        """
        flashobj is the flash device that we may operate on.
        """
        if not isinstance(confobj, config.Config):
            raise TypeError("confobj is not conf.Config. it is {}".
               format(type(confobj).__name__))

        self.conf = confobj

        self.n_entries_per_page = self.conf.dftl_n_mapping_entries_per_page()

        # do the easy thing first, if necessary, we can later use list or
        # other data structure
        self.entries = {}

    def total_entries(self):
        """
        total number of entries stored in global mapping table.  It is the same
        as the number of pages in flash, since we use page-leveling mapping
        """
        return self.conf.total_num_pages()

    def total_translation_pages(self):
        """
        total number of translation pages needed. It is:
        total_entries * entry size / page size
        """
        entries = self.total_entries()
        entry_bytes = self.conf['dftl']['global_mapping_entry_bytes']
        flash_page_size = self.conf['flash_page_size']
        # play the ceiling trick
        return (entries * entry_bytes + (flash_page_size -1))/flash_page_size

    def lpn_to_ppn(self, lpn):
        """
        GMT should always be able to answer query. It is perfectly OK to return
        None because at the beginning there is no mapping. No valid data block
        on device.
        """
        return self.entries.get(lpn, UNINITIATED)

    def update(self, lpn, ppn):
        self.entries[lpn] = ppn

    def __repr__(self):
        return "global mapping table: {}".format(repr(self.entries))


class GlobalTranslationDirectory(object):
    """
    This is an in-memory data structure. It is only for book keeping. It used
    to remeber thing so that we don't lose it.
    """
    def __init__(self, confobj):
        self.conf = confobj

        self.flash_npage_per_block = self.conf['flash_npage_per_block']
        self.flash_num_blocks = self.conf['flash_num_blocks']
        self.flash_page_size = self.conf['flash_page_size']
        self.total_pages = self.conf.total_num_pages()

        self.n_entries_per_page = self.conf.dftl_n_mapping_entries_per_page()

        # M_VPN -> M_PPN
        # Virtual translation page number --> Physical translation page number
        # Dftl should initialize
        self.mapping = {}

    def m_vpn_to_m_ppn(self, m_vpn):
        """
        m_vpn virtual translation page number. It should always be successfull.
        """
        return self.mapping[m_vpn]

    def add_mapping(self, m_vpn, m_ppn):
        if self.mapping.has_key(m_vpn):
            raise RuntimeError("self.mapping already has m_vpn:{}"\
                .format(m_vpn))
        self.mapping[m_vpn] = m_ppn

    def update_mapping(self, m_vpn, m_ppn):
        self.mapping[m_vpn] = m_ppn

    def remove_mapping(self, m_vpn):
        del self.mapping[m_vpn]

    def m_vpn_of_lpn(self, lpn):
        "Find the virtual translation page that holds lpn"
        return lpn / self.n_entries_per_page

    def m_vpn_to_lpns(self, m_vpn):
        start_lpn = m_vpn * self.n_entries_per_page
        return range(start_lpn, start_lpn + self.n_entries_per_page)

    def m_ppn_of_lpn(self, lpn):
        m_vpn = self.m_vpn_of_lpn(lpn)
        m_ppn = self.m_vpn_to_m_ppn(m_vpn)
        return m_ppn

    def __repr__(self):
        return repr(self.mapping)


class MappingManager(object):
    """
    This class is the supervisor of all the mappings. When initializing, it
    register CMT and GMT with it and provides higher level operations on top of
    them.
    This class should act as a coordinator of all the mapping data structures.
    """
    def __init__(self, confobj, block_pool, flashobj, oobobj, recorderobj):
        self.conf = confobj

        self.flash = flashobj
        self.oob = oobobj
        self.block_pool = block_pool
        self.recorder = recorderobj

        # managed and owned by Mappingmanager
        self.global_mapping_table = GlobalMappingTable(confobj, flashobj)
        self.cached_mapping_table = CachedMappingTable(confobj)
        self.directory = GlobalTranslationDirectory(confobj)

    def __del__(self):
        print self.flash.recorder.count_counter

    def lpn_to_ppn(self, lpn):
        """
        This method does not fail. It will try everything to find the ppn of
        the given lpn.
        return: real PPN or UNINITIATED
        """
        # try cached mapping table first.
        ppn = self.cached_mapping_table.lpn_to_ppn(lpn)
        if ppn == MISS:
            # cache miss
            while self.cached_mapping_table.is_full():
                self.evict_cache_entry()

            # find the physical translation page holding lpn's mapping in GTD
            ppn = self.load_mapping_entry_to_cache(lpn)

            self.recorder.count_me("cache", "miss")
        else:
            self.recorder.count_me("cache", "hit")

        return ppn

    def load_mapping_entry_to_cache(self, lpn):
        """
        When a mapping entry is not in cache, you need to read the entry from
        flash and put it to cache. This function does this.
        Output: it return the ppn of lpn read from entry on flash.
        """
        # find the location of the translation page
        m_ppn = self.directory.m_ppn_of_lpn(lpn)

        # read it up, this operation is just for statistics
        self.flash.page_read(m_ppn, TRANS_CACHE)

        # Now we have all the entries of m_ppn in memory, we need to put
        # the mapping of lpn->ppn to CMT
        ppn = self.global_mapping_table.lpn_to_ppn(lpn)
        self.cached_mapping_table.add_new_entry(lpn = lpn, ppn = ppn,
            dirty = False)

        return ppn

    def initialize_mappings(self):
        """
        This function initialize global translation directory. We assume the
        GTD is very small and stored in flash before mounting. We also assume
        that the global mapping table has been prepared by the vendor, so there
        is no other overhead except for reading the GTD from flash. Since the
        overhead is very small, we ignore it.
        """
        total_pages = self.global_mapping_table.total_translation_pages()

        # use some free blocks to be translation blocks
        tmp_blk_mapping = {}
        for m_vpn in range(total_pages):
            m_ppn = self.block_pool.next_translation_page_to_program()
            # Note that we don't actually read or write flash
            self.directory.add_mapping(m_vpn=m_vpn, m_ppn=m_ppn)
            # update oob of the translation page
            self.oob.new_write(lpn = m_vpn, old_ppn = UNINITIATED,
                new_ppn = m_ppn)

    def update_entry(self, lpn, new_ppn, tag = "NA"):
        """
        Update mapping of lpn to be lpn->new_ppn everywhere if necessary.

        if lpn is not in cache, it will NOT be added to it.

        block_pool:
            it may be affect because we need a new page
        CMT:
            if lpn is in cache, we need to update it and mark it as clean
            since after this function the cache will be consistent with GMT
        GMT:
            we need to read the old translation page, update it and write it
            to a new flash page
        OOB:
            we need to wipe out the old_ppn and fill the new_ppn
        GTD:
            we need to update m_vpn to new m_ppn
        """
        cached_ppn = self.cached_mapping_table.lpn_to_ppn(lpn)
        if cached_ppn != MISS:
            # in cache
            self.cached_mapping_table.overwrite_entry(lpn = lpn,
                ppn = new_ppn, dirty = False)

        m_vpn = self.directory.m_vpn_of_lpn(lpn)

        # batch_entries may be empty
        batch_entries = self.dirty_entries_of_translation_page(m_vpn)

        new_mappings = {lpn:new_ppn} # lpn->new_ppn may not be in cache
        for entry in batch_entries:
            new_mappings[entry.lpn] = entry.ppn

        # update translation page
        self.update_translation_page_on_flash(m_vpn, new_mappings, tag)

        # mark as clean
        for entry in batch_entries:
            entry.dirty = False

    def evict_cache_entry(self):
        """
        Select one entry in cache
        If the entry is dirty, write it back to GMT.
        If it is not dirty, simply remove it.
        """
        self.recorder.count_me('cache', 'evict')

        vic_lpn, vic_entrydata = self.cached_mapping_table.victim_entry()

        if vic_entrydata.dirty == True:
            # If we have to write to flash, we write in batch
            m_vpn = self.directory.m_vpn_of_lpn(vic_lpn)
            self.batch_write_back(m_vpn)

        # remove only the victim entry
        self.cached_mapping_table.remove_entry_by_lpn(vic_lpn)

    def batch_write_back(self, m_vpn):
        """
        Write dirty entries in a translation page with a flash read and a flash write.
        """
        self.recorder.count_me('cache', 'batch_write_back')

        batch_entries = self.dirty_entries_of_translation_page(m_vpn)

        new_mappings = {}
        for entry in batch_entries:
            new_mappings[entry.lpn] = entry.ppn

        # update translation page
        self.recorder.count_me('batch.size', len(new_mappings))
        self.update_translation_page_on_flash(m_vpn, new_mappings, TRANS_CACHE)

        # mark them as clean
        for entry in batch_entries:
            entry.dirty = False

    def dirty_entries_of_translation_page(self, m_vpn):
        """
        Get all dirty entries in translation page m_vpn.
        """
        retlist = []
        for entry_lpn, dataentry in self.cached_mapping_table.entries.items():
            if dataentry.dirty == True:
                tmp_m_vpn = self.directory.m_vpn_of_lpn(entry_lpn)
                if tmp_m_vpn == m_vpn:
                    retlist.append(dataentry)

        return retlist

    def update_translation_page_on_flash(self, m_vpn, new_mappings, tag):
        """
        Use new_mappings to replace their corresponding mappings in m_vpn

        read translationo page
        modify it with new_mappings
        write translation page to new location
        update related data structures

        Notes:
        - Note that it does not modify cached mapping table
        """
        old_m_ppn = self.directory.m_vpn_to_m_ppn(m_vpn)

        # update GMT on flash
        if len(new_mappings) < self.conf.dftl_n_mapping_entries_per_page():
            # need to read some mappings
            self.flash.page_read(old_m_ppn, tag)
        else:
            self.recorder.count_me('cache', 'saved.1.read')

        pass # modify in memory
        new_m_ppn = self.block_pool.next_translation_page_to_program()

        # update flash
        self.flash.page_write(new_m_ppn, tag)
        # update our fake 'on-flash' GMT
        for lpn, new_ppn in new_mappings.items():
            self.global_mapping_table.update(lpn = lpn, ppn = new_ppn)

        # OOB, keep m_vpn as lpn
        self.oob.new_write(lpn = m_vpn, old_ppn = old_m_ppn,
            new_ppn = new_m_ppn)

        # update GTD so we can find it
        self.directory.update_mapping(m_vpn = m_vpn, m_ppn = new_m_ppn)


class GcDecider(object):
    """
    It is used to decide wheter we should do garbage collection.

    When need_cleaning() is called the first time, use high water mark
    to decide if we need GC.
    Later, use low water mark and progress to decide. If we haven't make
    progress in 10 times, stop GC
    """
    def __init__(self, confobj, block_pool, recorderobj):
        self.conf = confobj
        self.block_pool = block_pool
        self.recorder = recorderobj
        self.call_index = -1

        self.high_watermark = self.conf['dftl']['GC_threshold_ratio'] * \
            self.conf['flash_num_blocks']
        self.low_watermark = self.conf['dftl']['GC_low_threshold_ratio'] * \
            self.conf['flash_num_blocks']

        self.last_used_blocks = None
        self.freeze_count = 0

    def need_cleaning(self):
        "The logic is a little complicated"
        self.call_index += 1

        n_used_blocks = self.block_pool.total_used_blocks()

        if self.call_index == 0:
            # clean when above high_watermark
            ret = n_used_blocks > self.high_watermark
        else:
            if self.freezed_too_long(n_used_blocks):
                ret = False
                self.recorder.count_me("GC", 'freezed_too_long')
            else:
                # common case
                ret = n_used_blocks > self.low_watermark
                if ret == False:
                    self.recorder.count_me("GC", 'below_lowerwatermark')

        return ret

    def improved(self, cur_n_used_blocks):
        """
        wether we get some free blocks since last call of this function
        """
        if self.last_used_blocks == None:
            ret = True
        else:
            # common case
            ret = cur_n_used_blocks < self.last_used_blocks

        self.last_used_blocks = cur_n_used_blocks
        return ret

    def freezed_too_long(self, cur_n_used_blocks):
        if self.improved(cur_n_used_blocks):
            self.freeze_count = 0
            ret = False
        else:
            self.freeze_count += 1

            if self.freeze_count > self.conf['flash_npage_per_block']:
                ret = True
            else:
                ret = False

        return ret


class BlockInfo(object):
    """
    This is for sorting blocks to clean the victim.
    """
    def __init__(self, block_type, block_num, value):
        self.block_type = block_type
        self.block_num = block_num
        self.value = value

    def __comp__(self, other):
        return cmp(self.value, other.value)


class GarbageCollector(object):
    def __init__(self, confobj, flashobj, oobobj, block_pool, mapping_manager,
        recorderobj):
        self.conf = confobj
        self.flash = flashobj
        self.oob = oobobj
        self.block_pool = block_pool
        self.recorder = recorderobj

        self.mapping_manager = mapping_manager

    def try_gc(self):
        decider = GcDecider(self.conf, self.block_pool, self.recorder)

        while decider.need_cleaning():
            if decider.call_index == 0:
                self.recorder.count_me("GC", "invoked")
                print 'GC is triggerred'
                block_iter = self.victim_blocks_iter()
            # victim_type, victim_block, valid_ratio = self.next_victim_block()
            # victim_type, victim_block, valid_ratio = \
                # self.next_victim_block_benefit_cost()
            try:
                blockinfo = block_iter.next()
            except StopIteration:
                self.recorder.count_me("GC", "StopIteration")
                # nothing to be cleaned
                break
            victim_type, victim_block = (blockinfo.block_type,
                blockinfo.block_num)
            if victim_type == DATA_BLOCK:
                self.clean_data_block(victim_block)
            elif victim_type == TRANS_BLOCK:
                self.clean_trans_block(victim_block)

    def clean_data_block(self, flash_block):
        self.move_valid_pages(flash_block, self.move_data_page_to_new_location)
        # mark block as free
        self.block_pool.move_used_data_block_to_free(flash_block)
        # it handles oob and flash
        self.erase_block(flash_block, DATA_CLEANING)

    def clean_trans_block(self, flash_block):
        self.move_valid_pages(flash_block,
            self.move_trans_page_to_new_location)
        # mark block as free
        self.block_pool.move_used_trans_block_to_free(flash_block)
        # it handles oob and flash
        self.erase_block(flash_block, TRANS_CLEAN)

    def move_valid_pages(self, flash_block, mover_func):
        start, end = self.conf.block_to_page_range(flash_block)

        for ppn in range(start, end):
            if self.oob.states.is_page_valid(ppn):
                mover_func(ppn)

    def move_data_page_to_new_location(self, ppn):
        """
        Page ppn must be valid.
        This function is for garbage collection. The difference between this
        one and the lba_write is that the input of this function is ppn, while
        lba_write's is lpn. Because of this, we don't need to consult mapping
        table to find the mapping. The lpn->ppn mapping is stored in OOB.
        Another difference is that, if the mapping exists in cache, update the
        entry in cache. If not, update the entry in GMT (and thus GTD).
        """
        # for my damaged brain
        old_ppn = ppn

        # read the the data page
        self.flash.page_read(old_ppn, DATA_CLEANING)

        # find the mapping
        lpn = self.oob.translate_ppn_to_lpn(old_ppn)

        # write to new page
        new_ppn = self.block_pool.next_gc_data_page_to_program()
        self.flash.page_write(new_ppn, DATA_CLEANING)

        # update new page and old page's OOB
        self.oob.new_write(lpn, old_ppn, new_ppn)

        cached_ppn = self.mapping_manager.cached_mapping_table.lpn_to_ppn(lpn)
        if cached_ppn == MISS:
            # This will not add mapping to cache
            self.mapping_manager.update_entry(lpn = lpn, new_ppn = new_ppn,
                tag = TRANS_CLEAN)
        else:
            # lpn is in cache, update it
            # This is a design from the original Dftl paper
            self.mapping_manager.cached_mapping_table.overwrite_entry(lpn = lpn,
                ppn = new_ppn, dirty = True)

    def move_trans_page_to_new_location(self, m_ppn):
        """
        1. read the trans page
        2. write to new location
        3. update OOB
        4. update GTD
        """
        old_m_ppn = m_ppn

        m_vpn = self.oob.translate_ppn_to_lpn(old_m_ppn)

        self.flash.page_read(old_m_ppn, TRANS_CLEAN)

        # write to new page
        new_m_ppn = self.block_pool.next_gc_translation_page_to_program()
        self.flash.page_write(new_m_ppn, TRANS_CLEAN)

        # update new page and old page's OOB
        self.oob.new_write(m_vpn, old_m_ppn, new_m_ppn)

        # update GTD
        self.mapping_manager.directory.update_mapping(m_vpn = m_vpn,
            m_ppn = new_m_ppn)

    def benefit_cost(self, blocknum, current_time):
        """
        This follows the DFTL paper
        """
        valid_ratio = self.oob.states.block_valid_ratio(blocknum)
        if valid_ratio == 0:
            # empty block is always the best deal
            return float("inf"), valid_ratio

        if valid_ratio == 1:
            # it is possible that none of the pages in the block has been
            # invalidated yet. In that case, all pages in the block is valid.
            # we don't need to clean it.
            return 0, valid_ratio

        last_inv_time = self.oob.last_inv_time_of_block.get(blocknum, None)
        if last_inv_time == None:
            print blocknum

        age = current_time - self.oob.last_inv_time_of_block[blocknum]
        age = age.total_seconds()
        bene_cost = age * ( 1 - valid_ratio ) / ( 2 * valid_ratio )

        return bene_cost, valid_ratio

    def next_victim_block_benefit_cost(self):
        """
        TODO: to improve, maintain a bene cost priority queue, so you
        don't need to compute repeatly
        """
        highest_bene_cost = -1
        ret_block = None
        block_type = None

        current_blocks = self.block_pool.current_blocks()

        current_time = datetime.datetime.now()

        for usedblocks, block_type in (
            (self.block_pool.data_usedblocks, DATA_BLOCK),
            (self.block_pool.trans_usedblocks, TRANS_BLOCK)):
            for blocknum in usedblocks:
                if blocknum in current_blocks:
                    continue

                bene_cost = self.bene_cost(blocknum, current_time)
                if bene_cost > highest_bene_cost:
                    ret_block = blocknum
                    highest_bene_cost = bene_cost
                    ret_valid_ratio = valid_ratio

        if ret_block == None:
            self.recorder.debug("no block is used yet.")

        return block_type, ret_block, ret_valid_ratio

    def victim_blocks_iter(self):
        """
        Calculate benefit/cost and put it to a priority queue
        """
        current_blocks = self.block_pool.current_blocks()
        current_time = datetime.datetime.now()
        priority_q = Queue.PriorityQueue()

        for usedblocks, block_type in (
            (self.block_pool.data_usedblocks, DATA_BLOCK),
            (self.block_pool.trans_usedblocks, TRANS_BLOCK)):
            for blocknum in usedblocks:
                if blocknum in current_blocks:
                    continue

                bene_cost, valid_ratio = self.benefit_cost(blocknum,
                    current_time)

                if bene_cost == 0:
                    # valid_ratio must be zero, we definitely don't
                    # want to cleaning it because we cannot get any
                    # free pages from it
                    continue

                blk_info = BlockInfo(block_type = block_type,
                    block_num = blocknum, value = bene_cost)
                blk_info.valid_ratio = valid_ratio

                if blk_info.valid_ratio > 0:
                    lpns = self.oob.lpns_of_block(blocknum)
                    s, e = self.conf.block_to_page_range(blocknum)
                    ppns = range(s, e)

                    ppn_states = [self.oob.states.page_state_human(ppn)
                        for ppn in ppns]
                    blk_info.mappings = zip(ppns, lpns, ppn_states)

                priority_q.put(blk_info)

        while not priority_q.empty():
            b_info =  priority_q.get()
            self.recorder.count_me('block.info.valid_ratio',
                round(b_info.valid_ratio, 2))
            self.recorder.count_me('block.info.bene_cost',
                round(b_info.value))

            if b_info.valid_ratio > 0:
                self.recorder.write_file('bad_victim_blocks',
                    block_type = b_info.block_type,
                    block_num = b_info.block_num,
                    bene_cost = b_info.value,
                    valid_ratio = round(b_info.valid_ratio, 2))

                # lpn ppn ppn_states blocknum
                for ppn, lpn, ppn_state in b_info.mappings:
                    self.recorder.write_file('bad.block.mappings',
                        ppn = ppn,
                        lpn = lpn,
                        ppn_state = ppn_state,
                        block_num = b_info.block_num,
                        valid_ratio = b_info.valid_ratio,
                        block_type = b_info.block_type
                        )

            yield b_info

    def erase_block(self, blocknum, tag):
        """
        THIS IS NOT A PUBLIC API
        set pages' oob states to ERASED
        electrionically erase the pages
        """
        # set page states to ERASED and in-OOB lpn to nothing
        self.oob.erase_block(blocknum)

        self.flash.block_erase(blocknum, tag)

def dec_debug(function):
    def wrapper(self, lpn):
        ret = function(self, lpn)
        if lpn == 38356:
            print function.__name__, 'lpn:', lpn, 'ret:', ret
        return ret
    return wrapper

#
# - translation pages
#   - cache miss read (trans.cache.load)
#   - eviction write  (trans.cache.evict)
#   - cleaning read   (trans.clean)
#   - cleaning write  (trans.clean)
# - data pages
#   - user read       (data.user)
#   - user write      (data.user)
#   - cleaning read   (data.cleaning)
#   - cleaning writes (data.cleaning)
# Tag format
# pagetype.
# Example tags:
TRANS_CACHE = "trans.cache" #read is due to miss, write is due to eviction
TRANS_CLEAN = "trans.clean" #read/write are for moving pages
DATA_USER = "data.user"
DATA_CLEANING = "data.cleaning"

class Dftl(ftlbuilder.FtlBuilder):
    """
    The implementation literally follows DFtl paper.
    This class is a coordinator of other coordinators and data structures
    """
    def __init__(self, confobj, recorderobj, flashobj):
        super(Dftl, self).__init__(confobj, recorderobj, flashobj)

        # bitmap has been created parent class
        # Change: we now don't put the bitmap here
        # self.bitmap.initialize()
        # del self.bitmap

        self.block_pool = BlockPool(confobj)
        self.oob = OutOfBandAreas(confobj)

        ###### the managers ######
        self.mapping_manager = MappingManager(
            confobj = self.conf,
            block_pool = self.block_pool,
            flashobj = flashobj,
            oobobj=self.oob,
            recorderobj = recorderobj
            )

        self.garbage_collector = GarbageCollector(
            confobj = self.conf,
            flashobj = flashobj,
            oobobj=self.oob,
            block_pool = self.block_pool,
            mapping_manager = self.mapping_manager,
            recorderobj = recorderobj
            )

        # We should initialize Globaltranslationdirectory in Dftl
        self.mapping_manager.initialize_mappings()

    # FTL APIs
    def lba_read(self, lpn):
        """
        ppn = translate(pagenum))
        flash.read(ppn)
        """
        self.recorder.put('logical_read', lpn, 'user')

        ppn = self.mapping_manager.lpn_to_ppn(lpn)
        self.flash.page_read(ppn, DATA_USER)

        self.garbage_collector.try_gc()

    def lba_write(self, lpn):
        """
        This is the interface for higher level to call, do NOT use it for
        internal use. If you need, create new one and refactor the code.

        block_pool
            no need to update
        CMT
            if lpn's mapping entry is in cache, update it and mark it as
            dirty. If it is not in cache, add such entry and mark as dirty
        GMT
            no need to update, it will be updated when we write back CMT
        OOB
            mark the new_ppn as valid
            update the LPN to lpn
            invalidate the old_ppn, go cleaner can GC it
            TODO: should the DFtl paper have considered the operation in OOB
        GTD
            No need to update, because GMT does not change
        Garbage collector
            We need to check if we need to do garbage collection
        Appending point
            It is automatically updated by next_data_page_to_program
        Flash
        """
        self.recorder.put('logical_write', lpn, 'user')

        old_ppn = self.mapping_manager.lpn_to_ppn(lpn)

        # appending point
        new_ppn = self.block_pool.next_data_page_to_program()

        # CMT
        # lpn must be in cache thanks to self.mapping_manager.lpn_to_ppn()
        self.mapping_manager.cached_mapping_table.overwrite_entry(
            lpn = lpn, ppn = new_ppn, dirty = True)

        # OOB
        self.oob.new_write(lpn = lpn, old_ppn = old_ppn,
            new_ppn = new_ppn)

        # Flash
        self.flash.page_write(new_ppn, DATA_USER)

        # garbage collection
        self.garbage_collector.try_gc()

    def lba_discard(self, lpn):
        """
        block_pool:
            no need to update
        CMT:
            if lpn->ppn exist, you need to update it to lpn->UNINITIATED
            if not exist, you need to add lpn->UNINITIATED
            the mapping lpn->UNINITIATED will be written back to GMT later
        GMT:
            no need to update
            REMEMBER: all updates to GMT can and only can be maded through CMT
        OOB:
            invalidate the ppn
            remove the lpn
        GTD:
            no updates needed
            updates should be done by GC

        """
        self.recorder.put('logical_discard', lpn, 'user')

        ppn = self.mapping_manager.lpn_to_ppn(lpn)
        if ppn == UNINITIATED:
            return

        # flash page ppn has valid data
        self.mapping_manager.cached_mapping_table.overwrite_entry(lpn = lpn,
            ppn = UNINITIATED, dirty = True)

        # OOB
        self.oob.wipe_ppn(ppn)

        # garbage collection checking and possibly doing
        self.garbage_collector.try_gc()


def main():
    pass

if __name__ == '__main__':
    main()

