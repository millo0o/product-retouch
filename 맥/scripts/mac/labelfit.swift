// AI 보정 사진(base)에 "글씨만 바로잡은 AI 라벨(patch)"을 정확히 겹쳐 맞춘다.
//  - 둘 다 AI가 만든 같은 장면이라 빛·질감이 같다. 원본 사진 조각은 쓰지 않는다.
//  - Vision 이미지 맞춤(호모그래피)으로 patch를 base 위치에 맞게 비틀고, 라벨(분홍) 안쪽만 부드러운 경계로 바꾼다.
//  - 결과는 base를 scale배 키운 크기로 저장 (라벨 글씨를 큰 해상도 그대로 살리려고).
// 사용법: labelfit <base.png> <patch.png> <x> <y> <w> <h> <out.png> [scale=2] [text|rect|label]
//   text = 글씨가 있는 곳 둘레만 바꿈(기본, 라벨 통째 다시 그린 것 겹칠 때)
//   rect = 잘라낸 네모 전체를 바꾸되 테두리는 부드럽게 (낱말 하나 고칠 때)
//   label = 분홍 라벨 안쪽만 (딸기베이스 시험 때 쓴 방식)
//   x y w h = patch가 원래 잘려 나온 base 안의 영역(왼쪽 위 기준, base 픽셀)
import Foundation; import Vision; import CoreImage; import simd

let a = CommandLine.arguments
guard a.count >= 8, let base0 = CIImage(contentsOf: URL(fileURLWithPath: a[1])), let patch0 = CIImage(contentsOf: URL(fileURLWithPath: a[2])),
      let x = Double(a[3]), let y = Double(a[4]), let w = Double(a[5]), let h = Double(a[6]) else { print("사용법: labelfit base patch x y w h out [scale]"); exit(1) }
let scale = a.count > 8 ? Double(a[8]) ?? 2 : 2
let cs = CGColorSpace(name: CGColorSpace.sRGB)!
let ctx = CIContext(options: [.workingColorSpace: cs, .outputColorSpace: cs])
func lz(_ i: CIImage, _ s: Double) -> CIImage { i.applyingFilter("CILanczosScaleTransform", parameters: ["inputScale": s, "inputAspectRatio": 1]) }

// 1. base를 키우고, patch 자리 영역(키운 좌표, CoreImage는 아래가 0)
let B = lz(base0, scale)
let BH = B.extent.height
let rect = CGRect(x: x * scale, y: BH - (y + h) * scale, width: w * scale, height: h * scale)
let ref = B.cropped(to: rect).transformed(by: CGAffineTransform(translationX: -rect.minX, y: -rect.minY))
// patch를 영역 크기로 대충 맞춘 뒤 정밀 맞춤
var P = patch0.transformed(by: CGAffineTransform(scaleX: rect.width / patch0.extent.width, y: rect.height / patch0.extent.height))
P = P.transformed(by: CGAffineTransform(translationX: -P.extent.minX, y: -P.extent.minY))

let req = VNHomographicImageRegistrationRequest(targetedCIImage: P)
try VNImageRequestHandler(ciImage: ref).perform([req])
guard let ob = req.results?.first as? VNImageHomographicAlignmentObservation else { print("맞춤 실패"); exit(2) }
let M = ob.warpTransform
func map(_ p: CGPoint) -> CGPoint {
    let v = M * simd_float3(Float(p.x), Float(p.y), 1)
    return CGPoint(x: CGFloat(v.x / v.z), y: CGFloat(v.y / v.z))
}
let W = P.extent.width, H = P.extent.height
let warped = P.applyingFilter("CIPerspectiveTransform", parameters: [
    "inputBottomLeft": CIVector(cgPoint: map(CGPoint(x: 0, y: 0))), "inputBottomRight": CIVector(cgPoint: map(CGPoint(x: W, y: 0))),
    "inputTopLeft": CIVector(cgPoint: map(CGPoint(x: 0, y: H))), "inputTopRight": CIVector(cgPoint: map(CGPoint(x: W, y: H)))]).cropped(to: ref.extent)
print("맞춤 변환:", (0..<3).map { c in (0..<3).map { r in String(format: "%.3f", M[c][r]) }.joined(separator: " ") }.joined(separator: " / "))

// 2. 라벨 영역 마스크: base 쪽 분홍 부분 → 구멍(글씨) 메우기 → 안쪽으로 줄이고 부드럽게
let rw = Int(ref.extent.width), rh = Int(ref.extent.height)
var buf = [UInt8](repeating: 0, count: rw * rh * 4)
ctx.render(ref, toBitmap: &buf, rowBytes: rw * 4, bounds: ref.extent, format: .RGBA8, colorSpace: cs)
var m = [UInt8](repeating: 0, count: rw * rh * 4)
for i in 0..<(rw * rh) {
    let r = Int(buf[i * 4]), g = Int(buf[i * 4 + 1]), b = Int(buf[i * 4 + 2])
    let pink = r - g > 18 && r - b > 12 && r > 120
    let v: UInt8 = pink ? 255 : 0
    m[i * 4] = v; m[i * 4 + 1] = v; m[i * 4 + 2] = v; m[i * 4 + 3] = 255
}
let mk = CIImage(bitmapData: Data(m), bytesPerRow: rw * 4, size: CGSize(width: rw, height: rh), format: .RGBA8, colorSpace: cs)
let close = Double(rw) / 40, shrink = Double(rw) / 150
let feather = max(4.0, Double(min(rw, rh)) / 10)
let rectMask = CIImage(color: .white).cropped(to: ref.extent.insetBy(dx: CGFloat(feather * 1.5), dy: CGFloat(feather * 1.5)))
    .composited(over: CIImage(color: .black).cropped(to: ref.extent))
    .applyingFilter("CIGaussianBlur", parameters: ["inputRadius": feather / 2]).cropped(to: ref.extent)
let mode = a.count > 9 ? a[9] : "text"
// text 모드: 글씨 줄 상자들을 줄 높이만큼 넓혀 합친 영역 (경계가 글씨 사이 빈 바탕에 오도록)
func textMask() -> CIImage {
    let req = VNDetectTextRectanglesRequest()
    try? VNImageRequestHandler(ciImage: ref).perform([req])
    let boxes = (req.results ?? []).map { $0.boundingBox }
    if boxes.isEmpty { return rectMask }
    let hs = boxes.map { $0.height * ref.extent.height }.sorted()
    let lh = max(6, hs[hs.count / 2])
    // 글씨 상자 전체를 감싸는 네모(줄 높이 1.5배 여유)를 부드러운 경계로 → 바코드·표·그림이 반반 섞이지 않게
    var u = CGRect.null
    for b in boxes { u = u.union(CGRect(x: b.minX * ref.extent.width, y: b.minY * ref.extent.height, width: b.width * ref.extent.width, height: b.height * ref.extent.height)) }
    u = u.insetBy(dx: -lh * 1.5, dy: -lh * 1.5).intersection(ref.extent.insetBy(dx: lh * 0.5, dy: lh * 0.5))
    return CIImage(color: .white).cropped(to: u).composited(over: CIImage(color: .black).cropped(to: ref.extent))
        .clampedToExtent().applyingFilter("CIGaussianBlur", parameters: ["inputRadius": lh * 0.4]).cropped(to: ref.extent)
}
let labelMask = mk.clampedToExtent()
    .applyingFilter("CIMorphologyMaximum", parameters: ["inputRadius": close])
    .applyingFilter("CIMorphologyMinimum", parameters: ["inputRadius": close + shrink])
    .applyingFilter("CIGaussianBlur", parameters: ["inputRadius": shrink * 0.6])
    .cropped(to: ref.extent)
let mask = mode == "rect" ? rectMask : mode == "label" ? labelMask : textMask()

// 3. 라벨 안쪽만 patch로, 바깥은 base 그대로
let blended = warped.applyingFilter("CIBlendWithMask", parameters: ["inputBackgroundImage": ref, "inputMaskImage": mask])
let placed = blended.transformed(by: CGAffineTransform(translationX: rect.minX, y: rect.minY))
let out = placed.composited(over: B).cropped(to: B.extent)
try ctx.writePNGRepresentation(of: out, to: URL(fileURLWithPath: a[7]), format: .RGBA8, colorSpace: cs)
// 확인용: 마스크도 저장
try ctx.writePNGRepresentation(of: mask, to: URL(fileURLWithPath: a[7] + ".mask.png"), format: .RGBA8, colorSpace: cs)
print("저장:", a[7], Int(out.extent.width), "x", Int(out.extent.height))
