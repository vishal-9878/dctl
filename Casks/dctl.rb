cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.4.0"
  sha256 arm:   "ccda1cd2556fc9fbce47d949db0fdcc5156db40ae584fd252ccf7f47b277b100",
         intel: "c050ffae4bb1d0bedd50cc976da2ee4d9287fa3657b18f0d963996700f86a3d8"

  url "https://github.com/vishal-9878/dctl/releases/download/v#{version}/dctl-#{version}-macos-#{arch}.pkg"
  name "dctl"
  desc "Agentless desktop app for Docker and PM2 on many servers, over SSH"
  homepage "https://github.com/vishal-9878/dctl"

  livecheck do
    url :url
    strategy :github_latest
  end

  pkg "dctl-#{version}-macos-#{arch}.pkg"

  uninstall pkgutil: "io.github.vishal-9878.dctl",
            delete:  [
              "/Applications/dctl.app",
              "/usr/local/bin/dctl",
              "/usr/local/bin/dctl-ui",
              "/usr/local/lib/dctl",
            ]

  zap trash: [
    "~/.cache/dctl",
    "~/.config/dctl",
  ]
end
