package io.github.jeonghyunk.adblock

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class BlocklistTest {

    @Before fun setUp() {
        Blocklist.loadFrom(
            """
            # 주석
            127.0.0.1 localhost
            0.0.0.0 0.0.0.0
            0.0.0.0 doubleclick.net
            0.0.0.0 ads.example.com   # 줄 끝 주석
            mobon.net
            """.trimIndent().reader().buffered()
        )
    }

    @Test fun parsesHostsFormat() {
        assertEquals(3, Blocklist.size)
    }

    @Test fun blocksExactAndSubdomains() {
        assertTrue(Blocklist.isBlocked("doubleclick.net"))
        assertTrue(Blocklist.isBlocked("googleads.g.doubleclick.net"))
        assertTrue(Blocklist.isBlocked("ADS.example.com."))
        assertTrue(Blocklist.isBlocked("img.mobon.net"))
    }

    @Test fun doesNotBlockNormalSites() {
        assertFalse(Blocklist.isBlocked("example.com"))
        assertFalse(Blocklist.isBlocked("www.example.com"))
        assertFalse(Blocklist.isBlocked("naver.com"))
        assertFalse(Blocklist.isBlocked("net"))
        assertFalse(Blocklist.isBlocked("localhost"))
    }

    @Test fun normalizeHandlesUrls() {
        assertEquals("ads.example.com", Blocklist.normalize(" https://Ads.Example.com/path "))
        assertEquals(null, Blocklist.normalize("   "))
    }
}
