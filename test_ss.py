import soccerdata as sd
# Tải dữ liệu từ FBref cho Ngoại hạng Anh mùa giải 25/26
fbref = sd.FBref(leagues="ENG-Premier League", seasons="2025/2026")
squad_stats = fbref.read_member_contingent() # Lấy danh sách thành viên đội hình