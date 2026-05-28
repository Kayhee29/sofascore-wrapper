import soccerdata as sd
import json
# Tải dữ liệu từ FBref cho Ngoại hạng Anh mùa giải 25/26
fbref = sd.FBref(leagues="ENG-Premier League", seasons="2025/2026")
squad_stats = fbref.read_player_season_stats(stat_type="standard")

# Reset index để dễ dàng thao tác lọc theo cột 'team'
df = squad_stats.reset_index()

# Lọc các cầu thủ của Arsenal
arsenal_df = df[df['team'] == 'Arsenal'].copy()

# Chọn và đổi tên các cột quan trọng để hiển thị sạch sẽ
# Lưu ý: soccerdata FBref trả về cột dạng MultiIndex hoặc flat tùy cấu hình. 
# Thường thì sau khi reset_index, các cột stats sẽ có dạng tuple hoặc flat.
# Hãy in danh sách cột ra trước hoặc xử lý an toàn.
print("Columns in DataFrame:")
print(list(arsenal_df.columns))

# Ghi nhận danh sách đầy đủ ra file JSON
arsenal_list = []
for _, row in arsenal_df.iterrows():
    player_name = row.get(('player', ''), 'Unknown')
    nation = row.get(('nation', ''), 'N/A')
    pos = row.get(('pos', ''), 'N/A')
    age = row.get(('age', ''), 'N/A')
    mp = row.get(('Playing Time', 'MP'), 0)
    starts = row.get(('Playing Time', 'Starts'), 0)
    min_played = row.get(('Playing Time', 'Min'), 0)
    gls = row.get(('Performance', 'Gls'), 0)
    ast = row.get(('Performance', 'Ast'), 0)
    
    # Chuẩn hóa quốc tịch (ví dụ "eng ENG" -> "ENG")
    if isinstance(nation, str) and " " in nation:
        nation = nation.split()[-1]
        
    # Chuẩn hóa tuổi (ví dụ 24-123 -> 24)
    age_str = str(age)
    if '-' in age_str:
        age_str = age_str.split('-')[0]

    # Kiểm tra xem các giá trị số có phải là NaN không (pandas.isna)
    import pandas as pd
    def clean_num(val):
        if pd.isna(val):
            return 0
        try:
            return int(val)
        except:
            return val

    arsenal_list.append({
        'Player': player_name,
        'Nation': nation,
        'Position': pos,
        'Age': age_str if age_str != 'nan' else 'N/A',
        'Matches Played': clean_num(mp),
        'Starts': clean_num(starts),
        'Minutes': clean_num(min_played),
        'Goals': clean_num(gls),
        'Assists': clean_num(ast)
    })

# Sắp xếp theo số phút thi đấu giảm dần (trụ cột lên đầu)
arsenal_list.sort(key=lambda x: int(x['Minutes']) if str(x['Minutes']).isdigit() else 0, reverse=True)

# Lưu kết quả đầy đủ
output_json = r"C:\Users\Kayhee29\sofascore-wrapper\arsenal_fbref_squad.json"
with open(output_json, 'w', encoding='utf-8') as f:
    json.dump(arsenal_list, f, ensure_ascii=False, indent=2)

print(f"\nSuccessfully saved {len(arsenal_list)} Arsenal players to {output_json}")

# In ra bảng Markdown đẹp mắt (Dùng nhãn tiếng Anh để tránh lỗi Unicode console trên Windows)
print("\n### ARSENAL FULL SQUAD (FBREF 2025/2026)")
print("| Player | Nation | Position | Age | MP (Starts) | Min | Goals | Assists |")
print("|---|---|---|---|---|---|---|---|")
for p in arsenal_list:
    # player_name có thể chứa ký tự unicode (như Ødegaard) nên ta cần encode an toàn hoặc in ra. 
    # Thường thì tên cầu thủ không gây lỗi trừ khi Windows console quá hạn chế.
    # Để an toàn nhất, ta dùng print thông thường nhưng hãy đổi tên Ødegaard hay các chữ Latin mở rộng nếu cần, 
    # tuy nhiên python 3 trên Windows thường hỗ trợ tốt latin mở rộng, chỉ bị lỗi với ký tự tiếng Việt (ĐỘI HÌNH).
    print(f"| {p['Player']} | {p['Nation']} | {p['Position']} | {p['Age']} | {p['Matches Played']} ({p['Starts']}) | {p['Minutes']} | {p['Goals']} | {p['Assists']} |")