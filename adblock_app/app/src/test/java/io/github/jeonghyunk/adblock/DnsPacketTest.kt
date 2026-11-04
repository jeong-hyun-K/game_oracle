package io.github.jeonghyunk.adblock

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class DnsPacketTest {

    /** 앱이 보내는 것과 같은 IPv4/UDP DNS 질의 패킷을 만듭니다. */
    private fun queryPacket(domain: String, qtype: Int, dstPort: Int = 53, proto: Int = 17): ByteArray {
        val qname = ArrayList<Byte>()
        for (label in domain.split('.')) {
            qname.add(label.length.toByte())
            label.forEach { qname.add(it.code.toByte()) }
        }
        qname.add(0)
        val dns = byteArrayOf(0x12, 0x34, 0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0) +
            qname.toByteArray() + byteArrayOf((qtype shr 8).toByte(), qtype.toByte(), 0, 1)
        val udpLen = 8 + dns.size
        val total = 20 + udpLen
        val p = ByteArray(total)
        p[0] = 0x45; p[2] = (total shr 8).toByte(); p[3] = total.toByte()
        p[8] = 64; p[9] = proto.toByte()
        byteArrayOf(10, 111, 222.toByte(), 1).copyInto(p, 12)     // 보낸 쪽: VPN 주소
        byteArrayOf(10, 111, 222.toByte(), 2).copyInto(p, 16)     // 받는 쪽: VPN DNS
        p[20] = 0xC3.toByte(); p[21] = 0x50                        // 출발 포트 50000
        p[22] = (dstPort shr 8).toByte(); p[23] = dstPort.toByte()
        p[24] = (udpLen shr 8).toByte(); p[25] = udpLen.toByte()
        dns.copyInto(p, 28)
        return p
    }

    @Test fun parsesDnsQuery() {
        val p = queryPacket("Ads.Example.com", DnsPacket.TYPE_A)
        val parsed = DnsPacket.parse(p, p.size)
        assertNotNull(parsed)
        val q = parsed!!
        assertEquals("ads.example.com", q.domain)
        assertEquals(DnsPacket.TYPE_A, q.qtype)
        assertEquals(50000, q.srcPort)
        assertEquals(53, q.dstPort)
    }

    @Test fun ignoresNonDnsPackets() {
        val tcp = queryPacket("a.com", 1, proto = 6)
        assertNull(DnsPacket.parse(tcp, tcp.size))
        val otherPort = queryPacket("a.com", 1, dstPort = 853)
        assertNull(DnsPacket.parse(otherPort, otherPort.size))
        assertNull(DnsPacket.parse(ByteArray(10), 10))
    }

    @Test fun blockedAnswerForAIsZeroAddress() {
        val p = queryPacket("ads.example.com", DnsPacket.TYPE_A)
        val q = DnsPacket.parse(p, p.size)!!
        val ans = DnsPacket.blockedAnswer(q)
        assertEquals(0x12, ans[0].toInt()); assertEquals(0x34, ans[1].toInt())   // 같은 ID
        assertEquals(0x81, ans[2].toInt() and 0xFF)                               // 응답 + RD
        assertEquals(0x80, ans[3].toInt() and 0xFF)                               // 오류 없음
        assertEquals(1, DnsPacket.u16(ans, 4))                                    // 질문 1
        assertEquals(1, DnsPacket.u16(ans, 6))                                    // 답 1
        val rdata = ans.copyOfRange(ans.size - 4, ans.size)
        assertArrayEquals(byteArrayOf(0, 0, 0, 0), rdata)
        assertEquals(4, DnsPacket.u16(ans, ans.size - 6))                         // RDLENGTH
    }

    @Test fun blockedAnswerForAaaaAndOthers() {
        val p6 = queryPacket("ads.example.com", DnsPacket.TYPE_AAAA)
        val a6 = DnsPacket.blockedAnswer(DnsPacket.parse(p6, p6.size)!!)
        assertEquals(1, DnsPacket.u16(a6, 6))
        assertEquals(16, DnsPacket.u16(a6, a6.size - 18))

        val https = queryPacket("ads.example.com", 65)
        val ah = DnsPacket.blockedAnswer(DnsPacket.parse(https, https.size)!!)
        assertEquals(0, DnsPacket.u16(ah, 6))                                     // 답 없음
    }

    @Test fun responsePacketIsValidIpv4Udp() {
        val p = queryPacket("ads.example.com", DnsPacket.TYPE_A)
        val q = DnsPacket.parse(p, p.size)!!
        val payload = DnsPacket.blockedAnswer(q)
        val r = DnsPacket.wrapResponse(q, payload)

        assertEquals(r.size, DnsPacket.u16(r, 2))
        // IP 헤더 체크섬 검증: 체크섬 포함 합계의 보수가 0
        assertEquals(0, DnsPacket.checksum(r, 0, 20, 0))
        // 주소와 포트가 뒤바뀌었는지
        assertArrayEquals(p.copyOfRange(16, 20), r.copyOfRange(12, 16))
        assertArrayEquals(p.copyOfRange(12, 16), r.copyOfRange(16, 20))
        assertEquals(53, DnsPacket.u16(r, 20))
        assertEquals(50000, DnsPacket.u16(r, 22))
        // UDP 체크섬 검증 (가상 헤더 포함)
        val udpLen = DnsPacket.u16(r, 24)
        var pseudo = 17L + udpLen
        for (i in 12 until 20 step 2) pseudo += DnsPacket.u16(r, i)
        assertEquals(0, DnsPacket.checksum(r, 20, udpLen, pseudo))
        assertArrayEquals(payload, r.copyOfRange(28, r.size))
    }
}
