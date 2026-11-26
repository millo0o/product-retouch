// 사진의 글씨를 읽는다 (맥 내장 Vision). 결과는 한 줄에 JSON 하나.
// 사용법: ocr <사진> [--scale 2] [--rot] [--crop x,y,w,h] [--find 글자]
//   --scale  읽기 전에 키우는 배율 (작은 글씨용)
//   --rot    90도 돌려서도 한 번 더 읽음 (세로 글씨, 바코드 숫자)
//   --crop   이 영역(원본 픽셀, 왼쪽 위 기준)만 읽음
//   --find   이 글자가 들어 있는 줄에서, 그 글자 자리만 상자로 돌려줌
// 출력: {"text","conf","x","y","w","h","rot"}  좌표는 원본 사진 픽셀, 왼쪽 위 기준
import Foundation; import Vision; import CoreImage

let a = CommandLine.arguments
func opt(_ k: String) -> String? { if let i = a.firstIndex(of: k), i + 1 < a.count { return a[i + 1] }; return nil }
guard a.count >= 2, let img0 = CIImage(contentsOf: URL(fileURLWithPath: a[1]), options: [.applyOrientationProperty: true]) else {
    print("사용법: ocr <사진> [--scale 2] [--rot] [--crop x,y,w,h] [--find 글자]"); exit(1) }
let FW = img0.extent.width, FH = img0.extent.height
var region = CGRect(x: 0, y: 0, width: FW, height: FH)          // 왼쪽 위 기준
if let c = opt("--crop") {
    let v = c.split(separator: ",").compactMap { Double($0) }
    if v.count == 4 { region = CGRect(x: v[0], y: v[1], width: v[2], height: v[3]).intersection(region) }
}
let scale = Double(opt("--scale") ?? "2") ?? 2
let find = opt("--find")
// CoreImage는 아래가 0
var img = img0.cropped(to: CGRect(x: region.minX, y: FH - region.maxY, width: region.width, height: region.height))
img = img.transformed(by: CGAffineTransform(translationX: -img.extent.minX, y: -img.extent.minY))
if scale != 1 { img = img.applyingFilter("CILanczosScaleTransform", parameters: ["inputScale": scale, "inputAspectRatio": 1]) }

func esc(_ s: String) -> String {
    let d = try! JSONSerialization.data(withJSONObject: [s]); let t = String(data: d, encoding: .utf8)!
    return String(t.dropFirst().dropLast())
}
func read(_ im: CIImage, rot: Bool, right: Bool = false) {
    let r = VNRecognizeTextRequest(); r.recognitionLevel = .accurate; r.usesLanguageCorrection = false
    r.recognitionLanguages = ["ko-KR", "en-US"]; r.minimumTextHeight = 0
    // 오른쪽으로 90도 돌린 그림을 읽게 하면(orientation .right) 위→아래로 쓰인 세로 글씨가 가로로 읽힌다
    let h = rot ? VNImageRequestHandler(ciImage: im, orientation: right ? .right : .left) : VNImageRequestHandler(ciImage: im)
    try? h.perform([r])
    for o in r.results ?? [] {
        guard let c = o.topCandidates(1).first else { continue }
        var b = o.boundingBox   // 정규화 좌표 (Vision이 원래 그림 기준으로 돌려줌)
        if let f = find {
            guard let rg = c.string.range(of: f), let bo = try? c.boundingBox(for: rg) else { continue }
            b = bo.boundingBox
        }
        // 돌려 읽은 경우 Vision 좌표는 돌린 그림 기준 → 원래 그림의 (왼쪽, 위, 폭, 높이) 비율로 되돌림
        var nx = b.minX, ny = 1 - b.maxY, nw = b.width, nh = b.height
        if rot {
            if right { nx = 1 - b.minY - b.height; ny = 1 - b.minX - b.width } else { nx = b.minY; ny = b.minX }
            nw = b.height; nh = b.width
            if nh * region.height < nw * region.width * 1.5 { continue }   // 가로 글씨는 이미 읽었음 (세로 글씨만)
        }
        let x = region.minX + nx * region.width, y = region.minY + ny * region.height
        print("{\"text\":\(esc(c.string)),\"conf\":\(String(format: "%.2f", c.confidence)),\"x\":\(Int(x)),\"y\":\(Int(y)),\"w\":\(Int(nw * region.width)),\"h\":\(Int(nh * region.height)),\"rot\":\(rot ? (right ? "\"right\"" : "\"left\"") : "false")}")
    }
}
read(img, rot: false)
if a.contains("--rot") { read(img, rot: true); read(img, rot: true, right: true) }
